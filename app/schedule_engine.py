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
                
                # Fetch device to get mac
                stmt_device = select(Artefacto).where(Artefacto.id == schedule.id_artefacto)
                res_device = await db.execute(stmt_device)
                dispositivo = res_device.scalar_one_or_none()
                
                if not dispositivo:
                    continue

                encender = False
                apagar = False

                # Compare hours/minutes ignoring seconds
                if schedule.hora_encendido and schedule.hora_encendido.hour == current_time.hour and schedule.hora_encendido.minute == current_time.minute:
                    if not dispositivo.estado_deseado:
                        encender = True

                if schedule.hora_apagado and schedule.hora_apagado.hour == current_time.hour and schedule.hora_apagado.minute == current_time.minute:
                    if dispositivo.estado_deseado:
                        apagar = True

                if encender or apagar:
                    from app.crud import comando_estado_con_lease
                    # We execute the change using lease, treating it as automated user action
                    accion = True if encender else False
                    await comando_estado_con_lease(
                        db, 
                        dispositivo.mac, 
                        encendido=accion, 
                        duracion_minutos=5, 
                        id_usuario=None
                    )
                    
                    # Also need to publish to MQTT
                    import paho.mqtt.publish as publish
                    import json
                    topic = f"smartups/dispositivos/{dispositivo.mac}/comando/estado"
                    payload = json.dumps({"encendido": accion})
                    credenciales_mqtt = {'username': settings.MQTT_USER, 'password': settings.MQTT_PASS}
                    publish.single(topic, payload, hostname=settings.MQTT_HOST, auth=credenciales_mqtt)
                    
                    logger.info(f"Horario ejecutado para {dispositivo.mac}: {'Encendido' if accion else 'Apagado'}")

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
