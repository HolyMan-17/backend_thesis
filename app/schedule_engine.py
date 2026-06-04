import asyncio
import logging
import traceback
from datetime import datetime, timezone, time
import zoneinfo

from sqlalchemy import select
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import AsyncSessionLocal
from app.models import ArtefactoHorario, Artefacto, EventoUsuario
from app.crud import comando_estado_con_lease, verificar_lease_activo
from app.config import settings

logger = logging.getLogger("uvicorn.error")

# In-memory store for the last calculated schedule state.
# Key: schedule_id (id_artefacto)
# Value: {
#     "should_be_on": bool,
#     "config_sig": tuple (days, start_min, end_min)
# }
_last_schedule_states = {}
_is_first_evaluation = True


def check_should_be_on(
    current_day: int,
    current_min: int,
    dias_operacion: list[int],
    start_min: int,
    end_min: int
) -> bool:
    """
    Computes whether the device should be ON under the given schedule parameters,
    supporting standard time windows as well as windows that span across midnight.
    """
    if not dias_operacion:
        return False

    # Case 1: Standard same-day window (e.g., 08:00 - 18:00)
    if start_min <= end_min:
        return (current_day in dias_operacion) and (start_min <= current_min < end_min)

    # Case 2: Midnight-spanning window (e.g., 22:00 - 06:00 next day)
    else:
        # Check if we are in the window that started today (current_min >= start_min)
        if current_min >= start_min:
            return current_day in dias_operacion
            
        # Check if we are in the window that started yesterday (current_min < end_min)
        elif current_min < end_min:
            yesterday_day = current_day - 1 if current_day > 1 else 7
            return yesterday_day in dias_operacion
            
        return False


async def _ejecutar_transicion(db: AsyncSession, dispositivo: Artefacto, encendido: bool) -> None:
    """
    Performs a single state transition: updates DB state (without setting user lease),
    publishes to MQTT, and sends push notifications.
    """
    try:
        stmt = (
            select(Artefacto)
            .where(Artefacto.id == dispositivo.id)
            .with_for_update()
        )
        result = await db.execute(stmt)
        dispositivo_db = result.scalar_one()

        dispositivo_db.estado_deseado = encendido
        if not encendido:
            dispositivo_db.auto_kill_at = None
        
        # Clear override lease as schedule transitions are automated and take precedence
        dispositivo_db.override_activo = False
        dispositivo_db.vencimiento_lease = None
        
        await db.flush()

        evento = EventoUsuario(
            id_artefacto=dispositivo_db.id,
            accion="comando_estado",
            razon_disparo=f"Relay {'encendido' if encendido else 'apagado'} según horario programado",
        )
        db.add(evento)

        await db.commit()
        await db.refresh(dispositivo_db)
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to update device state in transition for {dispositivo.mac}: {e}")
        raise

    # 2. Publish command via MQTT
    try:
        import paho.mqtt.publish as publish
        import json
        topic = f"smartups/dispositivos/{dispositivo.mac}/comando/estado"
        payload = json.dumps({"encendido": encendido})
        credenciales_mqtt = {'username': settings.MQTT_USER, 'password': settings.MQTT_PASS}
        publish.single(topic, payload, hostname=settings.MQTT_HOST, auth=credenciales_mqtt)
    except Exception as mq_err:
        logger.error(f"MQTT publish failed during schedule execution for {dispositivo.mac}: {mq_err}")
    
    # 3. Send Push Notification to owner admins
    try:
        from app.crud import enviar_push_a_duenos
        accion_str = "Encendido" if encendido else "Apagado"
        await enviar_push_a_duenos(
            db, dispositivo.mac,
            f"⏰ Automatización: {accion_str}",
            f"El dispositivo se ha {accion_str.lower()} según el horario programado."
        )
    except Exception as push_err:
        logger.error(f"Push notification failed during schedule execution for {dispositivo.mac}: {push_err}")
    
    logger.info(f"Horario ejecutado para {dispositivo.mac}: {'Encendido' if encendido else 'Apagado'}")


async def _evaluate_schedules() -> None:
    global _is_first_evaluation
    # Use America/Caracas timezone
    tz = zoneinfo.ZoneInfo("America/Caracas")
    now_local = datetime.now(tz)
    current_day = now_local.isoweekday() # 1 = Monday, 7 = Sunday
    current_time = now_local.time()
    current_min = current_time.hour * 60 + current_time.minute

    async with AsyncSessionLocal() as db:
        # Get all devices (even if offline) with automatizacion_activa=True
        # Eagerly load the related Artefacto to prevent N+1 queries.
        stmt = (
            select(ArtefactoHorario)
            .options(joinedload(ArtefactoHorario.artefacto))
            .join(Artefacto, Artefacto.id == ArtefactoHorario.id_artefacto)
            .where(
                Artefacto.deleted_at.is_(None),
                ArtefactoHorario.automatizacion_activa == True
            )
        )
        result = await db.execute(stmt)
        schedules = result.scalars().all()

        active_ids = set()

        for schedule in schedules:
            active_ids.add(schedule.id_artefacto)
            try:
                if not schedule.hora_encendido or not schedule.hora_apagado:
                    continue

                dispositivo = schedule.artefacto
                if not dispositivo:
                    continue

                start_min = schedule.hora_encendido.hour * 60 + schedule.hora_encendido.minute
                end_min = schedule.hora_apagado.hour * 60 + schedule.hora_apagado.minute

                # Evaluate time window (supporting midnight-spanning schedules)
                should_be_on = check_should_be_on(
                    current_day=current_day,
                    current_min=current_min,
                    dias_operacion=schedule.dias_operacion,
                    start_min=start_min,
                    end_min=end_min
                )

                # Signature of current schedule configuration to detect updates
                config_sig = (
                    tuple(sorted(schedule.dias_operacion)) if schedule.dias_operacion else (),
                    start_min,
                    end_min
                )

                prev_info = _last_schedule_states.get(schedule.id_artefacto)

                if prev_info is None:
                    # Case 1: First run / startup sync.
                    # If this is a global startup/reboot of the schedule engine,
                    # we seed the cache and run device shadow sync if desired differs from reported.
                    if _is_first_evaluation:
                        logger.info(f"Schedule engine startup: seeding cache for {dispositivo.mac} with state should_be_on={should_be_on}")
                        
                        # Device Shadow Startup Sync: if physical state (estado_reportado) differs
                        # from the desired state (estado_deseado), re-publish the command to get them in sync.
                        if dispositivo.estado_deseado != dispositivo.estado_reportado:
                            logger.info(f"Startup Device Shadow Sync: publishing state {dispositivo.estado_deseado} for {dispositivo.mac}")
                            try:
                                from paho.mqtt.publish import single as mqtt_publish_single
                                import json
                                topic = f"smartups/dispositivos/{dispositivo.mac}/comando/estado"
                                payload = json.dumps({"encendido": dispositivo.estado_deseado})
                                credenciales = {'username': settings.MQTT_USER, 'password': settings.MQTT_PASS}
                                mqtt_publish_single(topic, payload, hostname=settings.MQTT_HOST, auth=credenciales)
                            except Exception as err:
                                logger.error(f"Failed to publish startup device shadow sync for {dispositivo.mac}: {err}")

                        _last_schedule_states[schedule.id_artefacto] = {
                            "should_be_on": should_be_on,
                            "config_sig": config_sig
                        }
                    else:
                        # Otherwise, a schedule was newly activated/registered.
                        # Force sync immediately unless lease is active.
                        if should_be_on != dispositivo.estado_reportado:
                            lease_activo = await verificar_lease_activo(db, dispositivo.mac)
                            if lease_activo:
                                logger.info(f"New schedule sync skipped for {dispositivo.mac} due to active user override lease.")
                            else:
                                await _ejecutar_transicion(db, dispositivo, should_be_on)
                                _last_schedule_states[schedule.id_artefacto] = {
                                    "should_be_on": should_be_on,
                                    "config_sig": config_sig
                                }
                        else:
                            _last_schedule_states[schedule.id_artefacto] = {
                                "should_be_on": should_be_on,
                                "config_sig": config_sig
                            }

                elif prev_info["config_sig"] != config_sig:
                    # Case 2: Schedule was edited by the user.
                    # Force sync to the newly edited schedule immediately unless lease is active.
                    if should_be_on != dispositivo.estado_reportado:
                        lease_activo = await verificar_lease_activo(db, dispositivo.mac)
                        if lease_activo:
                            logger.info(f"Edited schedule sync skipped for {dispositivo.mac} due to active user override lease.")
                        else:
                            await _ejecutar_transicion(db, dispositivo, should_be_on)
                            _last_schedule_states[schedule.id_artefacto] = {
                                "should_be_on": should_be_on,
                                "config_sig": config_sig
                            }
                    else:
                        _last_schedule_states[schedule.id_artefacto] = {
                            "should_be_on": should_be_on,
                            "config_sig": config_sig
                        }

                else:
                    # Case 3: Normal runtime evaluation.
                    # Only execute a command when crossing the schedule transition boundary.
                    # This allows users to manually override the state inside or outside the window.
                    prev_should_be_on = prev_info["should_be_on"]
                    if should_be_on != prev_should_be_on:
                        lease_activo = await verificar_lease_activo(db, dispositivo.mac)
                        if lease_activo:
                            logger.info(f"Schedule transition skipped for {dispositivo.mac} due to active user override lease.")
                        else:
                            await _ejecutar_transicion(db, dispositivo, should_be_on)
                            _last_schedule_states[schedule.id_artefacto]["should_be_on"] = should_be_on

            except Exception as e:
                logger.error(f"Error evaluating schedule for device {schedule.id_artefacto}: {e}\n{traceback.format_exc()}")

        # Housekeeping: clean up cached schedules that are no longer active/deleted
        for key in list(_last_schedule_states.keys()):
            if key not in active_ids:
                _last_schedule_states.pop(key, None)

        # First evaluation completed
        if _is_first_evaluation:
            _is_first_evaluation = False

async def run_schedule_engine() -> None:
    logger.info("Schedule engine started")
    while True:
        try:
            # Sync to the next minute boundary + 0.5s for accurate, safe triggering
            now = datetime.now()
            sleep_seconds = 60 - (now.second + now.microsecond / 1000000.0) + 0.5
            await asyncio.sleep(sleep_seconds)
            
            await _evaluate_schedules()
        except Exception as e:
            logger.error(f"Schedule engine loop error: {e}")
            await asyncio.sleep(60)
