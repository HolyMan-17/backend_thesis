import asyncio
import json
import logging
import traceback
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models import Artefacto, Telemetria, Usuario, PermisoUsuarioArtefacto
from app.crud import (
    crear_recomendacion_si_necesario,
    resolver_recomendacion_auto,
    obtener_recomendaciones_resueltas_recientes,
    crear_evento,
    enviar_push_a_duenos,
)
from app.config import settings

logger = logging.getLogger("uvicorn.error")


async def _publish_mqtt(mac: str, payload: dict):
    try:
        import paho.mqtt.publish as publish
        publish.single(
            f"smartups/dispositivos/{mac}/comando/estado",
            json.dumps(payload),
            hostname=settings.MQTT_HOST,
            port=settings.MQTT_PORT,
            auth={"username": settings.MQTT_USER, "password": settings.MQTT_PASS},
        )
    except Exception as e:
        logger.error(f"MQTT publish error for {mac}: {e}")


async def _broadcast_event(mac: str, event_type: str, data: dict):
    try:
        from app.ws_manager import ws_manager
        await ws_manager.broadcast_event(mac, event_type, data)
    except Exception:
        pass


async def _fetch_recent_telemetry(
    db: AsyncSession,
    id_artefacto: int,
    minutes: int,
):
    # Normalize current time to timezone-naive UTC for MariaDB compatibility
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutes)
    stmt = (
        select(Telemetria.ai_status, Telemetria.voltaje, Telemetria.timestamp)
        .where(
            Telemetria.id_artefacto == id_artefacto,
            Telemetria.timestamp >= cutoff,
        )
        .order_by(Telemetria.timestamp.asc())
    )
    result = await db.execute(stmt)
    return result.all()


def _compute_metrics(rows, window_minutes: int):
    # Normalize current time to timezone-naive UTC for in-memory comparisons
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(minutes=window_minutes)

    # Cleanly strip tzinfo from row timestamps (if any) to prevent offset-naive/aware TypeError
    window_rows = []
    for ai, v, ts in rows:
        ts_naive = ts.replace(tzinfo=None) if (ts and getattr(ts, "tzinfo", None)) else ts
        if ts_naive >= cutoff:
            window_rows.append((ai, v, ts_naive))
    if not window_rows:
        return {
            "avg_ai": 0.0,
            "count": 0,
            "transitions": 0,
            "avg_voltage": 0.0,
            "sag_count": 0,
            "sag_events": 0,
        }

    avg_ai = sum(ai for ai, _, _ in window_rows) / len(window_rows)
    avg_voltage = sum(v for _, v, _ in window_rows) / len(window_rows)

    transitions = 0
    for i in range(1, len(window_rows)):
        if window_rows[i][0] != window_rows[i - 1][0]:
            transitions += 1

    sag_threshold = settings.RECOMMENDATION_VOLTAGE_BROWNOUT
    sag_count = sum(1 for _, v, _ in window_rows if v < sag_threshold)

    sag_events = 0
    for i in range(1, len(window_rows)):
        prev_above = window_rows[i - 1][1] >= sag_threshold
        curr_below = window_rows[i][1] < sag_threshold
        if prev_above and curr_below:
            sag_events += 1

    return {
        "avg_ai": avg_ai,
        "count": len(window_rows),
        "transitions": transitions,
        "avg_voltage": avg_voltage,
        "sag_count": sag_count,
        "sag_events": sag_events,
    }


def _device_label(artefacto: Artefacto) -> str:
    return artefacto.nombre_personalizado or artefacto.mac


async def _evaluate_consumo_riesgo_sostenido(
    db: AsyncSession, artefacto: Artefacto, rows: list
) -> None:
    tipo = "consumo_riesgo_sostenido"
    metrics = _compute_metrics(rows, settings.RECOMMENDATION_SUSTAINED_RISKY_MIN)
    min_count = max(3, settings.RECOMMENDATION_SUSTAINED_RISKY_MIN)

    if metrics["count"] >= min_count and metrics["avg_ai"] >= 1.0:
        label = _device_label(artefacto)
        await crear_recomendacion_si_necesario(
            db,
            artefacto.id,
            tipo,
            f"{label} shows sustained risky consumption (avg AI status: {metrics['avg_ai']:.1f}) for {settings.RECOMMENDATION_SUSTAINED_RISKY_MIN}+ min. Consider turning it off to preserve battery life.",
            "turn_off",
            "warning",
        )
    else:
        await resolver_recomendacion_auto(db, artefacto.id, tipo)


async def _evaluate_oscilacion_frecuente(
    db: AsyncSession, artefacto: Artefacto, rows: list
) -> None:
    tipo = "oscilacion_frecuente"
    metrics = _compute_metrics(rows, settings.RECOMMENDATION_OSCILLATION_WINDOW_MIN)

    if metrics["transitions"] >= settings.RECOMMENDATION_OSCILLATION_THRESHOLD:
        label = _device_label(artefacto)
        await crear_recomendacion_si_necesario(
            db,
            artefacto.id,
            tipo,
            f"{label} has {metrics['transitions']} status transitions in {settings.RECOMMENDATION_OSCILLATION_WINDOW_MIN} min. This may indicate an intermittent issue.",
            "investigate",
            "warning",
        )
    elif metrics["transitions"] < 2:
        await resolver_recomendacion_auto(db, artefacto.id, tipo)


async def _evaluate_recuperacion_consumo(
    db: AsyncSession, artefacto: Artefacto, rows: list
) -> None:
    tipo = "recuperacion_consumo"
    metrics = _compute_metrics(rows, settings.RECOMMENDATION_RECOVERY_SAFE_MIN)
    min_count = max(3, settings.RECOMMENDATION_RECOVERY_SAFE_MIN)

    if metrics["count"] >= min_count and metrics["avg_ai"] < 0.5:
        recientes = await obtener_recomendaciones_resueltas_recientes(
            db, artefacto.id, "consumo_riesgo_sostenido",
            horas=settings.RECOMMENDATION_RECOVERY_LOOKBACK_HOURS,
        )
        if recientes:
            label = _device_label(artefacto)
            await crear_recomendacion_si_necesario(
                db,
                artefacto.id,
                tipo,
                f"{label} has recovered to normal consumption after a recent risky episode.",
                None,
                "info",
            )


async def _evaluate_fluctuacion_voltaje(
    db: AsyncSession, artefacto: Artefacto, rows: list
) -> None:
    tipo = "fluctuacion_voltaje"
    short_metrics = _compute_metrics(rows, settings.RECOMMENDATION_SUSTAINED_RISKY_MIN)
    long_metrics = _compute_metrics(rows, settings.RECOMMENDATION_OSCILLATION_WINDOW_MIN)

    sustained_low = (
        short_metrics["count"] >= 3
        and short_metrics["avg_voltage"] < settings.RECOMMENDATION_VOLTAGE_BROWNOUT
    )
    frequent_sags = long_metrics["sag_events"] >= settings.RECOMMENDATION_VOLTAGE_SAG_COUNT

    if sustained_low or frequent_sags:
        label = _device_label(artefacto)
        reason = ""
        if sustained_low:
            reason = f"avg voltage {short_metrics['avg_voltage']:.1f}V below {settings.RECOMMENDATION_VOLTAGE_BROWNOUT:.0f}V threshold"
        elif frequent_sags:
            reason = f"{long_metrics['sag_events']} voltage sag events below {settings.RECOMMENDATION_VOLTAGE_BROWNOUT:.0f}V in {settings.RECOMMENDATION_OSCILLATION_WINDOW_MIN} min"

        await crear_recomendacion_si_necesario(
            db,
            artefacto.id,
            tipo,
            f"{label} is experiencing voltage instability: {reason}. Consider disconnecting to protect equipment.",
            "turn_off",
            "warning",
        )
    elif short_metrics["count"] >= 3 and short_metrics["avg_voltage"] >= settings.RECOMMENDATION_VOLTAGE_BROWNOUT:
        await resolver_recomendacion_auto(db, artefacto.id, tipo)


async def _get_owner_settings(db: AsyncSession, id_artefacto: int) -> Usuario | None:
    stmt = (
        select(Usuario)
        .join(PermisoUsuarioArtefacto, Usuario.id == PermisoUsuarioArtefacto.id_usuario)
        .where(PermisoUsuarioArtefacto.id_artefacto == id_artefacto)
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _handle_ai_control(
    db: AsyncSession, artefacto: Artefacto, rows: list, owner: Usuario
) -> None:
    # If device is already turned off physically or commanded off, do not schedule or execute auto-kill.
    # Cancel any active warning timer if it exists.
    if not artefacto.estado_reportado or not artefacto.estado_deseado:
        if artefacto.auto_kill_at:
            artefacto.auto_kill_at = None
            await db.commit()
            await _broadcast_event(artefacto.mac, "auto_kill_cancelled", {
                "message": f"Device is off, cancelling auto-kill warning.",
            })
            await enviar_push_a_duenos(
                db, artefacto.mac,
                "✅ Apagado IA Cancelado",
                f"El dispositivo se ha apagado, se canceló el apagado programado por IA."
            )
        return

    # Normalize current time and DB datetimes to timezone-naive UTC to prevent offset mismatch errors
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    
    ai_override_until = (
        artefacto.ai_override_until.replace(tzinfo=None)
        if (artefacto.ai_override_until and getattr(artefacto.ai_override_until, "tzinfo", None))
        else artefacto.ai_override_until
    )
    
    auto_kill_at = (
        artefacto.auto_kill_at.replace(tzinfo=None)
        if (artefacto.auto_kill_at and getattr(artefacto.auto_kill_at, "tzinfo", None))
        else artefacto.auto_kill_at
    )

    if ai_override_until and ai_override_until > now:
        if artefacto.auto_kill_at:
            artefacto.auto_kill_at = None
            await db.commit()
        return

    if auto_kill_at and auto_kill_at <= now:
        label = _device_label(artefacto)
        logger.warning(f"Auto-kill executing for {artefacto.mac} ({label})")

        artefacto.estado_deseado = False
        artefacto.auto_kill_at = None
        await db.commit()

        await _publish_mqtt(artefacto.mac, {"encendido": False})
        await _broadcast_event(artefacto.mac, "auto_kill_executed", {
            "message": f"{label} was automatically turned off to preserve battery.",
        })

        await crear_evento(
            db, id_artefacto=artefacto.id,
            accion="auto_kill",
            razon_disparo=f"Relay apagado automáticamente por IA (sustained RISKY)",
        )
        await enviar_push_a_duenos(
            db, artefacto.mac,
            "⚡ Dispositivo Apagado",
            f"El dispositivo {label} fue apagado automáticamente debido a consumo excesivo prolongado."
        )
        return

    if auto_kill_at and auto_kill_at > now:
        return

    short_metrics = _compute_metrics(rows, settings.AI_CONTROL_RISKY_THRESHOLD_MIN)
    min_count = max(2, settings.AI_CONTROL_RISKY_THRESHOLD_MIN)

    sustained_risky = short_metrics["count"] >= min_count and short_metrics["avg_ai"] >= 1.0

        if artefacto.auto_kill_at:
            artefacto.auto_kill_at = None
            await db.commit()
            await _broadcast_event(artefacto.mac, "auto_kill_cancelled", {
                "message": f"Risk condition cleared for {_device_label(artefacto)}.",
            })
            await enviar_push_a_duenos(
                db, artefacto.mac,
                "✅ Apagado IA Cancelado",
                f"El consumo de {_device_label(artefacto)} se normalizó y se canceló el apagado programado."
            )
        return

    label = _device_label(artefacto)

    if owner.auto_apagado_low_priority and artefacto.nivel_prioridad == "P3":
        logger.warning(f"P3 auto-kill executing for {artefacto.mac} ({label})")
        artefacto.estado_deseado = False
        await db.commit()

        await _publish_mqtt(artefacto.mac, {"encendido": False})
        await _broadcast_event(artefacto.mac, "auto_kill_executed", {
            "message": f"{label} (P3) was automatically turned off to preserve battery.",
        })

        await crear_evento(
            db, id_artefacto=artefacto.id,
            accion="auto_kill",
            razon_disparo=f"Relay apagado automáticamente (P3 auto-apagado, AI status RISKY)",
        )
        await enviar_push_a_duenos(
            db, artefacto.mac,
            "⚡ Dispositivo Apagado",
            f"El dispositivo {label} (P3) fue apagado automáticamente debido a consumo excesivo prolongado."
        )
        return

    if owner.ai_control_habilitado:
        artefacto.auto_kill_at = now + timedelta(minutes=settings.AI_CONTROL_GRACE_PERIOD_MIN)
        await db.commit()

        await _broadcast_event(artefacto.mac, "auto_kill_warning", {
            "auto_kill_at": artefacto.auto_kill_at.isoformat(),
            "grace_period_min": settings.AI_CONTROL_GRACE_PERIOD_MIN,
            "message": f"⚠️ High drain detected on {label}. It will be automatically turned off in {settings.AI_CONTROL_GRACE_PERIOD_MIN} minutes.",
            "accion_sugerida": "keep_on",
        })
        await enviar_push_a_duenos(
            db, artefacto.mac,
            "⚠️ Apagado IA Programado",
            f"El dispositivo {label} se apagará automáticamente en {settings.AI_CONTROL_GRACE_PERIOD_MIN} minutos por consumo excesivo."
        )


async def _evaluate_device(db: AsyncSession, artefacto: Artefacto) -> None:
    try:
        rows = await _fetch_recent_telemetry(
            db, artefacto.id, minutes=settings.RECOMMENDATION_OSCILLATION_WINDOW_MIN
        )
        if not rows:
            return

        await _evaluate_consumo_riesgo_sostenido(db, artefacto, rows)
        await _evaluate_oscilacion_frecuente(db, artefacto, rows)
        await _evaluate_recuperacion_consumo(db, artefacto, rows)
        await _evaluate_fluctuacion_voltaje(db, artefacto, rows)

        owner = await _get_owner_settings(db, artefacto.id)
        if owner is None:
            return

        if not owner.ai_control_habilitado and not owner.auto_apagado_low_priority:
            return

        if owner.auto_apagado_low_priority and artefacto.nivel_prioridad == "P3":
            await _handle_ai_control(db, artefacto, rows, owner)
        elif owner.ai_control_habilitado:
            await _handle_ai_control(db, artefacto, rows, owner)
    except Exception as e:
        logger.error(f"Error evaluating recommendations for device: {e}\n{traceback.format_exc()}")


async def scan_all_devices() -> None:
    # 1. Fetch the list of active devices using a short-lived database session
    async with AsyncSessionLocal() as db:
        stmt = (
            select(Artefacto.mac)
            .where(
                Artefacto.is_online == True,
                Artefacto.deleted_at.is_(None),
            )
        )
        result = await db.execute(stmt)
        device_macs = result.scalars().all()

    # 2. Evaluate each device in its own independent database session
    # This prevents commit() side-effects (like ORM expiration/lazy-loading) from bleeding between devices
    for mac in device_macs:
        try:
            async with AsyncSessionLocal() as db:
                stmt = (
                    select(Artefacto)
                    .where(
                        Artefacto.mac == mac,
                        Artefacto.deleted_at.is_(None),
                    )
                    .options(selectinload(Artefacto.limites))
                )
                result = await db.execute(stmt)
                device = result.scalar_one_or_none()
                if device:
                    await _evaluate_device(db, device)
        except Exception as e:
            logger.error(f"Failed to evaluate device {mac} in dedicated session: {e}\n{traceback.format_exc()}")


async def run_recommendation_engine() -> None:
    logger.info("Recommendation engine started")
    while True:
        try:
            await scan_all_devices()
        except Exception as e:
            logger.error(f"Recommendation engine scan error: {e}")
        await asyncio.sleep(settings.RECOMMENDATION_SCAN_INTERVAL)