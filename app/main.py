from typing import List
import json
import logging
from contextlib import asynccontextmanager
import paho.mqtt.publish as publish
from fastapi import FastAPI, Depends, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import settings
from app.schemas import (
    TelemetriaCreate, TelemetriaResponse,
    DispositivoCreate, DispositivoUpdate, DispositivoResponse,
    ComandoEstado, ComandoLimites,
    DispositivoEstado, DispositivoLimites, DispositivoEstadoResponse,
    UserSyncRequest,
)
from app.crud import (
    crear_telemetria, obtener_telemetria_por_mac,
    actualizar_estado_dispositivo, obtener_estado_dispositivo,
    sincronizar_usuario, verificar_acceso,
    obtener_dispositivos_usuario, obtener_dispositivo_por_mac,
    crear_dispositivo, actualizar_dispositivo,
)
from app.mqtt_listener import iniciar_oyente_mqtt
from app.auth import get_current_user, verify_sync_secret
from app.exceptions import (
    AppException, NotFoundException, ForbiddenException,
    app_exception_handler, validation_exception_handler,
)
from app.models import Usuario
from fastapi.exceptions import RequestValidationError

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cliente_mqtt = iniciar_oyente_mqtt()
    yield
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
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    rows = await obtener_dispositivos_usuario(db, user.id)
    result = []
    for artefacto, nivel_acceso in rows:
        result.append(DispositivoResponse(
            id=artefacto.id,
            mac=artefacto.mac,
            nombre_personalizado=artefacto.nombre_personalizado,
            nivel_prioridad=artefacto.nivel_prioridad,
            limite_consumo_w=float(artefacto.limite_consumo_w),
            is_online=artefacto.is_online,
            is_encendido=artefacto.is_encendido,
            nivel_acceso=nivel_acceso,
            last_seen_at=artefacto.last_seen_at,
        ))
    return result


@app.post("/api/dispositivos", response_model=DispositivoResponse, status_code=201)
async def registrar_dispositivo(
    device_in: DispositivoCreate,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    existing = await obtener_dispositivo_por_mac(db, device_in.mac)
    if existing:
        raise ForbiddenException(message="Dispositivo ya registrado", mac=device_in.mac)

    artefacto = await crear_dispositivo(db, device_in.mac, user.id)
    return DispositivoResponse(
        id=artefacto.id,
        mac=artefacto.mac,
        nombre_personalizado=artefacto.nombre_personalizado,
        nivel_prioridad=artefacto.nivel_prioridad,
        limite_consumo_w=float(artefacto.limite_consumo_w),
        is_online=artefacto.is_online,
        is_encendido=artefacto.is_encendido,
        nivel_acceso="ADMIN",
        last_seen_at=artefacto.last_seen_at,
    )


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

    # Get access level
    rows = await obtener_dispositivos_usuario(db, user.id)
    nivel_acceso = "ADMIN"
    for artefacto, nivel in rows:
        if artefacto.mac == mac:
            nivel_acceso = nivel
            break

    return DispositivoResponse(
        id=row.id,
        mac=row.mac,
        nombre_personalizado=row.nombre_personalizado,
        nivel_prioridad=row.nivel_prioridad,
        limite_consumo_w=float(row.limite_consumo_w),
        is_online=row.is_online,
        is_encendido=row.is_encendido,
        nivel_acceso=nivel_acceso,
        last_seen_at=row.last_seen_at,
    )


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

    return DispositivoResponse(
        id=updated.id,
        mac=updated.mac,
        nombre_personalizado=updated.nombre_personalizado,
        nivel_prioridad=updated.nivel_prioridad,
        limite_consumo_w=float(updated.limite_consumo_w),
        is_online=updated.is_online,
        is_encendido=updated.is_encendido,
        nivel_acceso="ADMIN",
        last_seen_at=updated.last_seen_at,
    )


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

    exito_bd = await actualizar_estado_dispositivo(db, mac, comando.encendido)
    if not exito_bd:
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

    estado = await obtener_estado_dispositivo(db, mac)
    if estado is None:
        raise NotFoundException(message="Dispositivo no encontrado", mac=mac)

    parametros_actualizados = limites.model_dump(exclude_unset=True)

    topic = f"smartups/dispositivos/{mac}/comando/limites"
    payload = json.dumps(parametros_actualizados)
    credenciales_mqtt = {'username': settings.MQTT_USER, 'password': settings.MQTT_PASS}
    publish.single(topic, payload, hostname=settings.MQTT_HOST, auth=credenciales_mqtt)

    return {}


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

    exito_bd = await actualizar_estado_dispositivo(db, comando.mac_dispositivo, comando.encendido)
    if not exito_bd:
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

    estado = await obtener_estado_dispositivo(db, limites.mac_dispositivo)
    if estado is None:
        raise NotFoundException(message="Dispositivo no encontrado", mac=limites.mac_dispositivo)

    parametros_actualizados = limites.model_dump(exclude_unset=True, exclude={'mac_dispositivo'})

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

    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Echo: {data}")
    except WebSocketDisconnect:
        pass