from typing import List, Optional
from datetime import datetime, timezone, timedelta
import asyncio
import json
import logging
from contextlib import asynccontextmanager
import paho.mqtt.publish as publish
from fastapi import FastAPI, Depends, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import settings
from app.schemas import (
    TelemetriaCreate, TelemetriaResponse,
    DispositivoCreate, DispositivoUpdate, DispositivoResponse,
    ComandoEstado, ComandoLimites,
    DispositivoEstado, DispositivoLimites, DispositivoEstadoResponse,
    UserSyncRequest, UserSettingsUpdate, UserSettingsResponse,
    AlertaResponse, AlertaUpdate,
    RecomendacionResponse, RecomendacionUpdate,
    EventoResponse,
    AgregadoResponse, AgregadoQuery,
    HorarioUpdate, HorarioResponse,
)
from app.crud import (
    crear_telemetria, obtener_telemetria_por_mac,
    comando_estado_con_lease,
    actualizar_dispositivo, obtener_dispositivo_por_mac,
    obtener_estado_dispositivo,
    sincronizar_usuario, verificar_acceso,
    obtener_dispositivos_usuario,
    crear_dispositivo, eliminar_dispositivo,
    obtener_agregados_telemetria,
    obtener_alertas_usuario, marcar_alerta_resuelta,
    obtener_recomendaciones_usuario, marcar_recomendacion_resuelta,
    cancelar_auto_kill, actualizar_settings_usuario,
    crear_evento, obtener_eventos_usuario,
    obtener_horario_dispositivo, actualizar_horario_dispositivo,
)
from app.mqtt_listener import iniciar_oyente_mqtt
from app.recommendation_engine import run_recommendation_engine
from app.schedule_engine import run_schedule_engine
from app.ws_manager import ws_manager
from app.auth import get_current_user, verify_sync_secret
from app.exceptions import (
    AppException, NotFoundException, ForbiddenException,
    app_exception_handler, validation_exception_handler,
)
from app.models import Usuario, Artefacto, Recomendacion
from fastapi.exceptions import RequestValidationError

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cliente_mqtt = iniciar_oyente_mqtt()
    engine_task = asyncio.create_task(run_recommendation_engine())
    schedule_task = asyncio.create_task(run_schedule_engine())
    yield
    engine_task.cancel()
    schedule_task.cancel()
    cliente_mqtt.loop_stop()
    cliente_mqtt.disconnect()


app = FastAPI(title="SmartSaver API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:19006",
        "http://localhost:3000",
        "http://localhost:8081",
        "exp://127.0.0.1:8081",
        "smartsaver://callback",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)


# --- HELPER ---

def artefacto_to_response(artefacto, nivel_acceso: str = "ADMIN") -> DispositivoResponse:
    limites = artefacto.limites
    return DispositivoResponse(
        id=artefacto.id,
        mac=artefacto.mac,
        nombre_personalizado=artefacto.nombre_personalizado,
        nivel_prioridad=artefacto.nivel_prioridad,
        limite_consumo_w=float(limites.limite_consumo_w) if limites else 0.0,
        limite_voltaje=float(limites.limite_voltaje) if limites and limites.limite_voltaje is not None else None,
        limite_corriente=float(limites.limite_corriente) if limites and limites.limite_corriente is not None else None,
        limite_potencia=float(limites.limite_potencia) if limites and limites.limite_potencia is not None else None,
        estado_deseado=artefacto.estado_deseado,
        estado_reportado=artefacto.estado_reportado,
        is_online=artefacto.is_online,
        nivel_acceso=nivel_acceso,
        last_seen_at=artefacto.last_seen_at,
        auto_kill_at=artefacto.auto_kill_at,
        automatizacion_activa=artefacto.horario.automatizacion_activa if artefacto.horario else False,
    )


# --- PUBLIC ENDPOINTS ---

@app.get("/health")
async def health_check():
    return {}


# --- AUTH0 WEBHOOK ---

@app.post("/api/users/sync")
async def sync_user(
    sync_in: UserSyncRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    verify_sync_secret(request)
    usuario = await sincronizar_usuario(db, sync_in)
    return {"status": "synced", "auth0_id": usuario.auth0_id}


# --- USER SETTINGS ---

@app.get("/api/users/settings", response_model=UserSettingsResponse)
async def get_user_settings(
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    return UserSettingsResponse(
        ai_control_habilitado=user.ai_control_habilitado,
        auto_apagado_low_priority=user.auto_apagado_low_priority,
    )


@app.patch("/api/users/settings", response_model=UserSettingsResponse)
async def update_user_settings(
    settings_in: UserSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    datos = settings_in.model_dump(exclude_unset=True)
    if not datos:
        raise AppException(error="validation_error", message="No fields to update", status_code=422)

    usuario = await actualizar_settings_usuario(db, user.id, datos)
    if not usuario:
        raise NotFoundException(message="Usuario no encontrado")

    return UserSettingsResponse(
        ai_control_habilitado=usuario.ai_control_habilitado,
        auto_apagado_low_priority=usuario.auto_apagado_low_priority,
    )


# --- M2M ENDPOINTS (no JWT) ---

@app.post("/api/telemetria", response_model=TelemetriaResponse, status_code=201)
async def registrar_telemetria(
    telemetria_in: TelemetriaCreate,
    db: AsyncSession = Depends(get_db),
):
    nueva_telemetria = await crear_telemetria(db, telemetria_in)
    if nueva_telemetria is None:
        raise NotFoundException(message="Dispositivo no registrado")
    return nueva_telemetria


# --- DEVICE CRUD ---

@app.get("/api/dispositivos", response_model=List[DispositivoResponse])
async def listar_dispositivos(
    prioridad: Optional[str] = Query(default=None, pattern=r"^P[1-3]$"),
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    rows = await obtener_dispositivos_usuario(db, user.id, prioridad=prioridad)
    return [artefacto_to_response(artefacto, nivel_acceso) for artefacto, nivel_acceso in rows]


@app.post("/api/dispositivos", response_model=DispositivoResponse, status_code=201)
async def registrar_dispositivo(
    device_in: DispositivoCreate,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    if await verificar_acceso(db, user.id, device_in.mac):
        raise ForbiddenException(message="Dispositivo ya registrado a este usuario", mac=device_in.mac)

    artefacto = await crear_dispositivo(db, device_in.mac, user.id)
    return artefacto_to_response(artefacto)


@app.get("/api/dispositivos/{mac}", response_model=DispositivoResponse)
async def obtener_dispositivo(
    mac: str,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    if not await verificar_acceso(db, user.id, mac):
        raise ForbiddenException(message="Dispositivo no autorizado", mac=mac)

    row = await obtener_dispositivo_por_mac(db, mac)
    if not row:
        raise NotFoundException(message="Dispositivo no encontrado", mac=mac)

    rows = await obtener_dispositivos_usuario(db, user.id)
    nivel_acceso = "ADMIN"
    for artefacto, nivel in rows:
        if artefacto.mac == mac:
            nivel_acceso = nivel
            break

    return artefacto_to_response(row, nivel_acceso)


@app.patch("/api/dispositivos/{mac}", response_model=DispositivoResponse)
async def actualizar_dispositivo_endpoint(
    mac: str,
    device_in: DispositivoUpdate,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    if not await verificar_acceso(db, user.id, mac):
        raise ForbiddenException(message="Dispositivo no autorizado", mac=mac)

    datos = device_in.model_dump(exclude_unset=True)
    updated = await actualizar_dispositivo(db, mac, datos)
    if not updated:
        raise NotFoundException(message="Dispositivo no encontrado", mac=mac)

    return artefacto_to_response(updated)


@app.delete("/api/dispositivos/{mac}")
async def eliminar_dispositivo_endpoint(
    mac: str,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    if not await verificar_acceso(db, user.id, mac):
        raise ForbiddenException(message="Dispositivo no autorizado", mac=mac)

    eliminado = await eliminar_dispositivo(db, mac)
    if not eliminado:
        raise NotFoundException(message="Dispositivo no encontrado", mac=mac)

    return {"status": "deleted", "mac": mac}


# --- TELEMETRY (under device) ---

@app.get("/api/dispositivos/{mac}/telemetria", response_model=List[TelemetriaResponse])
async def leer_telemetria(
    mac: str,
    limite: int = 50,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    if not await verificar_acceso(db, user.id, mac):
        raise ForbiddenException(message="Dispositivo no autorizado", mac=mac)
    datos = await obtener_telemetria_por_mac(db, mac=mac, limite=limite)
    return datos if datos is not None else []


@app.get("/api/dispositivos/{mac}/agregados", response_model=List[AgregadoResponse])
async def obtener_agregados(
    mac: str,
    granularity: str = Query(default="hour", pattern=r"^(hour|day)$"),
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    if not await verificar_acceso(db, user.id, mac):
        raise ForbiddenException(message="Dispositivo no autorizado", mac=mac)

    result = await obtener_agregados_telemetria(db, mac, granularity, desde, hasta)
    if result is None:
        raise NotFoundException(message="Dispositivo no encontrado", mac=mac)

    items = []
    for row in result:
        bucket_str = row["bucket"]
        bucket_dt = datetime.strptime(bucket_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        items.append(AgregadoResponse(
            bucket=bucket_dt,
            potencia_promedio_w=float(row["potencia_promedio_w"]) if row["potencia_promedio_w"] is not None else 0.0,
            potencia_maxima_w=float(row["potencia_maxima_w"]) if row["potencia_maxima_w"] is not None else 0.0,
            energia_wh=float(row["energia_wh"]) if row["energia_wh"] is not None else 0.0,
        ))
    return items


# --- COMMANDS (under device) ---

@app.post("/api/dispositivos/{mac}/comando/estado")
async def comando_estado(
    mac: str,
    comando: ComandoEstado,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    if not await verificar_acceso(db, user.id, mac):
        raise ForbiddenException(message="Dispositivo no autorizado", mac=mac)

    dispositivo = await obtener_dispositivo_por_mac(db, mac)
    if not dispositivo:
        raise NotFoundException(message="Dispositivo no encontrado", mac=mac)

    if dispositivo.horario and dispositivo.horario.automatizacion_activa:
        import zoneinfo
        from datetime import datetime
        tz = zoneinfo.ZoneInfo("America/Caracas")
        now_local = datetime.now(tz)
        current_day = now_local.isoweekday()
        current_minutes = now_local.hour * 60 + now_local.minute
        
        in_schedule = False
        if dispositivo.horario.dias_operacion and current_day in dispositivo.horario.dias_operacion:
            if dispositivo.horario.hora_encendido and dispositivo.horario.hora_apagado:
                start_min = dispositivo.horario.hora_encendido.hour * 60 + dispositivo.horario.hora_encendido.minute
                end_min = dispositivo.horario.hora_apagado.hour * 60 + dispositivo.horario.hora_apagado.minute
                if start_min <= current_minutes < end_min:
                    in_schedule = True
                    
        if in_schedule:
            if not comando.override_automation:
                raise AppException(error="automation_active", message="El dispositivo está operando dentro del horario establecido. Se requiere override_automation=true para proceder.", status_code=409)
            # Disable automation
            await actualizar_horario_dispositivo(db, mac, {"automatizacion_activa": False})

    dispositivo = await comando_estado_con_lease(
        db, mac, comando.encendido, duracion_minutos=5, id_usuario=user.id,
    )
    if not dispositivo:
        raise NotFoundException(message="Dispositivo no encontrado", mac=mac)

    topic = f"smartups/dispositivos/{mac}/comando/estado"
    payload = json.dumps({"encendido": comando.encendido})
    credenciales_mqtt = {'username': settings.MQTT_USER, 'password': settings.MQTT_PASS}
    publish.single(topic, payload, hostname=settings.MQTT_HOST, auth=credenciales_mqtt)

    return {}


@app.post("/api/dispositivos/{mac}/comando/limites")
async def comando_limites(
    mac: str,
    limites: ComandoLimites,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    if not await verificar_acceso(db, user.id, mac):
        raise ForbiddenException(message="Dispositivo no autorizado", mac=mac)

    estado = await obtener_dispositivo_por_mac(db, mac)
    if estado is None:
        raise NotFoundException(message="Dispositivo no encontrado", mac=mac)

    datos = limites.model_dump(exclude_unset=True)
    if datos:
        await actualizar_dispositivo(db, mac, datos)

    topic = f"smartups/dispositivos/{mac}/comando/limites"
    payload = json.dumps(datos)
    credenciales_mqtt = {'username': settings.MQTT_USER, 'password': settings.MQTT_PASS}
    publish.single(topic, payload, hostname=settings.MQTT_HOST, auth=credenciales_mqtt)

    return {}


# --- HORARIOS ---

@app.get("/api/dispositivos/{mac}/horario", response_model=HorarioResponse)
async def obtener_horario(
    mac: str,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    if not await verificar_acceso(db, user.id, mac):
        raise ForbiddenException(message="Dispositivo no autorizado", mac=mac)

    estado = await obtener_dispositivo_por_mac(db, mac)
    if estado is None:
        raise NotFoundException(message="Dispositivo no encontrado", mac=mac)

    horario = await obtener_horario_dispositivo(db, mac)
    if not horario:
        # Devolver horario por defecto vacío
        return HorarioResponse(
            dias_operacion=[],
            hora_encendido=None,
            hora_apagado=None,
            automatizacion_activa=False,
            id_artefacto=estado.id,
            actualizado_en=datetime.now(timezone.utc)
        )
    return horario


@app.put("/api/dispositivos/{mac}/horario", response_model=HorarioResponse)
async def actualizar_horario(
    mac: str,
    horario_in: HorarioUpdate,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    if not await verificar_acceso(db, user.id, mac):
        raise ForbiddenException(message="Dispositivo no autorizado", mac=mac)

    estado = await obtener_dispositivo_por_mac(db, mac)
    if estado is None:
        raise NotFoundException(message="Dispositivo no encontrado", mac=mac)

    datos = horario_in.model_dump(exclude_unset=True)
    horario = await actualizar_horario_dispositivo(db, mac, datos)

    # --- Immediate evaluation: if automation is now active, check if the device
    # should be on or off RIGHT NOW and send the MQTT command immediately.
    if horario.automatizacion_activa and horario.hora_encendido and horario.hora_apagado and horario.dias_operacion:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("America/Caracas")
        now_local = datetime.now(tz)
        current_day = now_local.isoweekday()
        current_minutes = now_local.hour * 60 + now_local.minute

        if current_day in horario.dias_operacion:
            start_min = horario.hora_encendido.hour * 60 + horario.hora_encendido.minute
            end_min = horario.hora_apagado.hour * 60 + horario.hora_apagado.minute

            should_be_on = start_min <= current_minutes < end_min
            device_is_on = estado.estado_deseado

            if should_be_on != device_is_on:
                await comando_estado_con_lease(
                    db, mac, encendido=should_be_on, duracion_minutos=5, id_usuario=user.id
                )
                topic = f"smartups/dispositivos/{mac}/comando/estado"
                payload = json.dumps({"encendido": should_be_on})
                credenciales_mqtt = {'username': settings.MQTT_USER, 'password': settings.MQTT_PASS}
                publish.single(topic, payload, hostname=settings.MQTT_HOST, auth=credenciales_mqtt)
                logger.info(f"Horario guardado para {mac}: estado inmediato {'Encendido' if should_be_on else 'Apagado'}")

    return horario


# --- AI CONTROL ---

@app.post("/api/dispositivos/{mac}/ai-control/override")
async def ai_control_override(
    mac: str,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    if not await verificar_acceso(db, user.id, mac):
        raise ForbiddenException(message="Dispositivo no autorizado", mac=mac)

    dispositivo = await cancelar_auto_kill(db, mac, cooldown_minutes=settings.AI_CONTROL_OVERRIDE_COOLDOWN_MIN)
    if not dispositivo:
        raise NotFoundException(message="Dispositivo no encontrado", mac=mac)

    await crear_evento(
        db, id_artefacto=dispositivo.id, id_usuario=user.id,
        accion="ai_override",
        razon_disparo=f"Usuario canceló auto-kill para {mac}",
    )

    return {"status": "overridden", "mac": mac, "ai_override_until": dispositivo.ai_override_until.isoformat() if dispositivo.ai_override_until else None}


# --- ALERTS ---

@app.get("/api/alertas", response_model=List[AlertaResponse])
async def listar_alertas(
    solo_activas: bool = True,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    alertas = await obtener_alertas_usuario(db, user.id, solo_activas=solo_activas)
    return alertas


@app.patch("/api/alertas/{alerta_id}", response_model=AlertaResponse)
async def resolver_alerta(
    alerta_id: int,
    alerta_in: AlertaUpdate,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    if not alerta_in.resuelto:
        raise AppException(error="validation_error", message="Only resolving alerts is supported", status_code=422)

    alerta = await marcar_alerta_resuelta(db, alerta_id, user.id)
    if not alerta:
        raise NotFoundException(message="Alerta no encontrada", alerta_id=alerta_id)

    return alerta


# --- RECOMMENDATIONS ---

@app.get("/api/recomendaciones", response_model=List[RecomendacionResponse])
async def listar_recomendaciones(
    solo_activas: bool = True,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    recomendaciones = await obtener_recomendaciones_usuario(db, user.id, solo_activas=solo_activas)
    results = []
    for rec in recomendaciones:
        artefacto = await db.execute(
            select(Artefacto).where(Artefacto.id == rec.id_artefacto)
        )
        device = artefacto.scalar_one_or_none()
        rec_dict = RecomendacionResponse.model_validate(rec)
        if device:
            rec_dict.mac_dispositivo = device.mac
            rec_dict.nombre_personalizado = device.nombre_personalizado
        results.append(rec_dict)
    return results


@app.patch("/api/recomendaciones/{recomendacion_id}", response_model=RecomendacionResponse)
async def resolver_recomendacion(
    recomendacion_id: int,
    rec_in: RecomendacionUpdate,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    if not rec_in.resuelto:
        raise AppException(error="validation_error", message="Only resolving recommendations is supported", status_code=422)

    rec = await marcar_recomendacion_resuelta(db, recomendacion_id, user.id)
    if not rec:
        raise NotFoundException(message="Recomendacion no encontrada", recomendacion_id=recomendacion_id)

    result = RecomendacionResponse.model_validate(rec)
    artefacto = await db.execute(
        select(Artefacto).where(Artefacto.id == rec.id_artefacto)
    )
    device = artefacto.scalar_one_or_none()
    if device:
        result.mac_dispositivo = device.mac
        result.nombre_personalizado = device.nombre_personalizado
    return result


# --- EVENTS ---

@app.get("/api/eventos", response_model=List[EventoResponse])
async def listar_eventos(
    mac: Optional[str] = None,
    limite: int = 50,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    eventos = await obtener_eventos_usuario(db, user.id, mac=mac, limite=limite)
    return eventos


# --- LEGACY ENDPOINTS (to be removed after frontend migration) ---

@app.get("/api/telemetria/{mac_dispositivo}", response_model=List[TelemetriaResponse])
async def leer_telemetria_legacy(
    mac_dispositivo: str,
    limite: int = 50,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    if not await verificar_acceso(db, user.id, mac_dispositivo):
        raise ForbiddenException(message="Dispositivo no autorizado", mac=mac_dispositivo)
    datos = await obtener_telemetria_por_mac(db, mac=mac_dispositivo, limite=limite)
    return datos if datos is not None else []


@app.post("/api/comando/estado")
async def comando_estado_legacy(
    comando: DispositivoEstado,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    if not await verificar_acceso(db, user.id, comando.mac_dispositivo):
        raise ForbiddenException(message="Dispositivo no autorizado", mac=comando.mac_dispositivo)

    dispositivo = await comando_estado_con_lease(
        db, comando.mac_dispositivo, comando.encendido, duracion_minutos=5, id_usuario=user.id,
    )
    if not dispositivo:
        raise NotFoundException(message="Dispositivo no encontrado", mac=comando.mac_dispositivo)

    topic = f"smartups/dispositivos/{comando.mac_dispositivo}/comando/estado"
    payload = json.dumps({"encendido": comando.encendido})
    credenciales_mqtt = {'username': settings.MQTT_USER, 'password': settings.MQTT_PASS}
    publish.single(topic, payload, hostname=settings.MQTT_HOST, auth=credenciales_mqtt)

    return {}


@app.post("/api/comando/limites")
async def comando_limites_legacy(
    limites: DispositivoLimites,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    if not await verificar_acceso(db, user.id, limites.mac_dispositivo):
        raise ForbiddenException(message="Dispositivo no autorizado", mac=limites.mac_dispositivo)

    estado = await obtener_dispositivo_por_mac(db, limites.mac_dispositivo)
    if estado is None:
        raise NotFoundException(message="Dispositivo no encontrado", mac=limites.mac_dispositivo)

    parametros_actualizados = limites.model_dump(exclude_unset=True, exclude={'mac_dispositivo'})
    if parametros_actualizados:
        await actualizar_dispositivo(db, limites.mac_dispositivo, parametros_actualizados)

    topic = f"smartups/dispositivos/{limites.mac_dispositivo}/comando/limites"
    payload = json.dumps(parametros_actualizados)
    credenciales_mqtt = {'username': settings.MQTT_USER, 'password': settings.MQTT_PASS}
    publish.single(topic, payload, hostname=settings.MQTT_HOST, auth=credenciales_mqtt)

    return {}


@app.get("/api/dispositivos/{mac_dispositivo}/estado")
async def leer_estado_dispositivo_legacy(
    mac_dispositivo: str,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    if not await verificar_acceso(db, user.id, mac_dispositivo):
        raise ForbiddenException(message="Dispositivo no autorizado", mac=mac_dispositivo)

    is_online = await obtener_estado_dispositivo(db, mac_dispositivo)
    if is_online is None:
        raise NotFoundException(message="Dispositivo no encontrado", mac=mac_dispositivo)

    return {"mac_dispositivo": mac_dispositivo, "is_online": bool(is_online)}


# --- WEBSOCKET ---

@app.websocket("/ws/telemetry")
async def websocket_telemetry(
    websocket: WebSocket,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    from app.auth import _fetch_jwks, _get_signing_key
    from jose import JWTError, jwt

    try:
        unverified_header = jwt.get_unverified_header(token)
        jwks = await _fetch_jwks()
        signing_key = _get_signing_key(unverified_header, jwks)
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.AUTH0_AUDIENCE,
            issuer=settings.AUTH0_ISSUER,
        )
        auth0_id = payload.get("sub")
        if not auth0_id:
            await websocket.close(code=4001, reason="Unauthorized: missing sub")
            return
    except Exception:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    from app.models import Usuario as UsuarioModel

    stmt = select(UsuarioModel).where(UsuarioModel.auth0_id == auth0_id, UsuarioModel.activo == True)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        await websocket.close(code=4001, reason="User not found")
        return

    rows = await obtener_dispositivos_usuario(db, user.id)
    allowed_macs = {artefacto.mac for artefacto, _ in rows}

    await websocket.accept()
    await ws_manager.connect(websocket, allowed_macs)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(websocket)
