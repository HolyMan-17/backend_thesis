import asyncio
import logging
import traceback
from datetime import datetime, timezone
import zoneinfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import AsyncSessionLocal
from app.models import ArtefactoHorario, Artefacto
from app.crud import comando_estado_con_lease
from app.config import settings

logger = logging.getLogger("uvicorn.error")

async def _evaluate_schedules() -> None:
    # Use America/Caracas timezone
    tz = zoneinfo.ZoneInfo("America/Caracas")
    now_local = datetime.now(tz)
    current_day = now_local.isoweekday() # 1 = Monday, 7 = Sunday
    current_time = now_local.time()

    async with AsyncSessionLocal() as db:
        # Get all online devices with automatizacion_activa=True
        stmt = (
            select(ArtefactoHorario)
            .join(Artefacto, Artefacto.id == ArtefactoHorario.id_artefacto)
            .where(
                Artefacto.is_online == True,
                Artefacto.deleted_at.is_(None),
                ArtefactoHorario.automatizacion_activa == True
            )
        )
        result = await db.execute(stmt)
        schedules = result.scalars().all()

        for schedule in schedules:
            try:
                # Check if current day is in dias_operacion
                if not schedule.dias_operacion or current_day not in schedule.dias_operacion:
                    continue
                
                if not schedule.hora_encendido or not schedule.hora_apagado:
                    continue

                # Fetch device to get mac
                stmt_device = select(Artefacto).where(Artefacto.id == schedule.id_artefacto)
                res_device = await db.execute(stmt_device)
                dispositivo = res_device.scalar_one_or_none()
                
                if not dispositivo:
                    continue

                # Evaluate the full time window, not just exact minute boundaries.
                # This makes the engine self-correcting: if it misses a tick (server
                # restart, lag), it will fix the state on the next evaluation.
                start_min = schedule.hora_encendido.hour * 60 + schedule.hora_encendido.minute
                end_min = schedule.hora_apagado.hour * 60 + schedule.hora_apagado.minute
                current_min = current_time.hour * 60 + current_time.minute

                should_be_on = start_min <= current_min < end_min
                device_is_on = dispositivo.estado_deseado

                if should_be_on != device_is_on:
                    from app.crud import comando_estado_con_lease
                    await comando_estado_con_lease(
                        db, 
                        dispositivo.mac, 
                        encendido=should_be_on, 
                        duracion_minutos=5, 
                        id_usuario=None
                    )
                    
                    # Also need to publish to MQTT
                    import paho.mqtt.publish as publish
                    import json
                    topic = f"smartups/dispositivos/{dispositivo.mac}/comando/estado"
                    payload = json.dumps({"encendido": should_be_on})
                    credenciales_mqtt = {'username': settings.MQTT_USER, 'password': settings.MQTT_PASS}
                    publish.single(topic, payload, hostname=settings.MQTT_HOST, auth=credenciales_mqtt)
                    
                    # Enviar Push
                    from app.crud import enviar_push_a_duenos
                    accion_str = "Encendido" if should_be_on else "Apagado"
                    await enviar_push_a_duenos(
                        db, dispositivo.mac,
                        f"⏰ Automatización: {accion_str}",
                        f"El dispositivo se ha {accion_str.lower()} según el horario programado."
                    )
                    
                    logger.info(f"Horario ejecutado para {dispositivo.mac}: {accion_str}")

            except Exception as e:
                logger.error(f"Error evaluating schedule for device {schedule.id_artefacto}: {e}\n{traceback.format_exc()}")

async def run_schedule_engine() -> None:
    logger.info("Schedule engine started")
    while True:
        try:
            # Sync to the next minute boundary for accurate triggering
            now = datetime.now()
            sleep_seconds = 60 - now.second
            await asyncio.sleep(sleep_seconds)
            
            await _evaluate_schedules()
        except Exception as e:
            logger.error(f"Schedule engine loop error: {e}")
            await asyncio.sleep(60)
