from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Artefacto, Telemetria, Usuario, PermisoUsuarioArtefacto
from app.schemas import TelemetriaCreate, UserSyncRequest

async def crear_telemetria(db: AsyncSession, telemetria_in: TelemetriaCreate):
    # Map the incoming MAC to the Artefacto table
    stmt = select(Artefacto).where(Artefacto.mac == telemetria_in.mac_dispositivo)
    result = await db.execute(stmt)
    dispositivo = result.scalar_one_or_none()

    if not dispositivo:
        return None

    nueva_metrica = Telemetria(
        id_artefacto=dispositivo.id,
        voltaje=telemetria_in.voltaje,
        corriente=telemetria_in.corriente,
        potencia=telemetria_in.potencia,
        tiempo_operacion_s=telemetria_in.tiempo_operacion_s,
        estado_sin_cambios=False 
    )
    
    db.add(nueva_metrica)
    await db.commit()
    await db.refresh(nueva_metrica)
    nueva_metrica.mac_dispositivo = dispositivo.mac
    
    return nueva_metrica

async def obtener_telemetria_por_mac(db: AsyncSession, mac: str, limite: int = 50):
    stmt = (
        select(Telemetria, Artefacto.mac)
        .join(Artefacto, Telemetria.id_artefacto == Artefacto.id)
        .where(Artefacto.mac == mac)
        .order_by(Telemetria.timestamp.desc())
        .limit(limite)
    )
    result = await db.execute(stmt)
    rows = result.all()
    items = []
    for row in rows:
        t = row[0]
        t.mac_dispositivo = row[1]
        items.append(t)
    return items

async def actualizar_estado_dispositivo(db: AsyncSession, mac: str, encendido: bool):
    stmt = select(Artefacto).where(Artefacto.mac == mac)
    result = await db.execute(stmt)
    dispositivo = result.scalar_one_or_none()
    
    if not dispositivo:
        return False
        
    # FIX: Update the physical relay state, NOT the reachability state.
    # is_online will remain untouched, preserving the device's network status.
    dispositivo.is_encendido = encendido
    await db.commit()
    
    return True

async def actualizar_online_dispositivo(db: AsyncSession, mac: str, online: bool):
    stmt = select(Artefacto).where(Artefacto.mac == mac)
    result = await db.execute(stmt)
    dispositivo = result.scalar_one_or_none()

    if not dispositivo:
        return False

    dispositivo.is_online = online
    dispositivo.last_seen_at = func.now()
    await db.commit()

    return True

async def obtener_estado_dispositivo(db: AsyncSession, mac: str):
    stmt = select(Artefacto).where(Artefacto.mac == mac)
    result = await db.execute(stmt)
    dispositivo = result.scalar_one_or_none()
    
    if not dispositivo:
        return None
        
    return dispositivo.is_online


async def sincronizar_usuario(db: AsyncSession, sync_in: UserSyncRequest) -> Usuario:
    stmt = select(Usuario).where(Usuario.auth0_id == sync_in.auth0_id)
    result = await db.execute(stmt)
    usuario = result.scalar_one_or_none()

    if usuario:
        usuario.email = sync_in.email
        if sync_in.nombre is not None:
            usuario.nombre = sync_in.nombre
        usuario.ultimo_acceso = func.now()
    else:
        usuario = Usuario(
            auth0_id=sync_in.auth0_id,
            email=sync_in.email,
            nombre=sync_in.nombre,
        )
        db.add(usuario)

    await db.commit()
    await db.refresh(usuario)
    return usuario


async def obtener_dispositivos_usuario(db: AsyncSession, user_id: int):
    stmt = (
        select(Artefacto, PermisoUsuarioArtefacto.nivel_acceso)
        .join(PermisoUsuarioArtefacto, Artefacto.id == PermisoUsuarioArtefacto.id_artefacto)
        .where(PermisoUsuarioArtefacto.id_usuario == user_id)
    )
    result = await db.execute(stmt)
    return result.all()


async def obtener_dispositivo_con_acceso(db: AsyncSession, mac: str, user_id: int):
    stmt = (
        select(Artefacto, PermisoUsuarioArtefacto.nivel_acceso)
        .join(PermisoUsuarioArtefacto, Artefacto.id == PermisoUsuarioArtefacto.id_artefacto)
        .where(Artefacto.mac == mac, PermisoUsuarioArtefacto.id_usuario == user_id)
    )
    result = await db.execute(stmt)
    return result.first()


async def verificar_acceso(db: AsyncSession, user_id: int, mac: str) -> bool:
    stmt = (
        select(PermisoUsuarioArtefacto)
        .join(Artefacto, PermisoUsuarioArtefacto.id_artefacto == Artefacto.id)
        .where(PermisoUsuarioArtefacto.id_usuario == user_id, Artefacto.mac == mac)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def obtener_dispositivo_por_mac(db: AsyncSession, mac: str) -> Artefacto | None:
    stmt = select(Artefacto).where(Artefacto.mac == mac)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def crear_dispositivo(db: AsyncSession, mac: str, user_id: int) -> Artefacto:
    dispositivo = Artefacto(
        mac=mac,
        nombre_personalizado=None,
        nivel_prioridad="media",
        limite_consumo_w=0,
        is_online=False,
        is_encendido=False,
    )
    db.add(dispositivo)
    await db.commit()
    await db.refresh(dispositivo)

    permiso = PermisoUsuarioArtefacto(
        id_usuario=user_id,
        id_artefacto=dispositivo.id,
        nivel_acceso="ADMIN",
    )
    db.add(permiso)
    await db.commit()

    return dispositivo


async def actualizar_dispositivo(db: AsyncSession, mac: str, datos: dict) -> Artefacto | None:
    stmt = select(Artefacto).where(Artefacto.mac == mac)
    result = await db.execute(stmt)
    dispositivo = result.scalar_one_or_none()

    if not dispositivo:
        return None

    if "nombre_personalizado" in datos and datos["nombre_personalizado"] is not None:
        dispositivo.nombre_personalizado = datos["nombre_personalizado"]
    if "nivel_prioridad" in datos and datos["nivel_prioridad"] is not None:
        dispositivo.nivel_prioridad = datos["nivel_prioridad"]
    if "limite_consumo_w" in datos and datos["limite_consumo_w"] is not None:
        dispositivo.limite_consumo_w = datos["limite_consumo_w"]

    await db.commit()
    await db.refresh(dispositivo)
    return dispositivo
