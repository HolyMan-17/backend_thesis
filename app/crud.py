from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone, timedelta
from app.models import (
    Artefacto, ArtefactoLimite, Telemetria, Usuario, PermisoUsuarioArtefacto,
    AlertaSistema, EventoUsuario, Recomendacion,
)
from app.schemas import TelemetriaCreate, UserSyncRequest


# ---------------------------------------------------------------------------
# TELEMETRY
# ---------------------------------------------------------------------------

async def crear_telemetria(db: AsyncSession, telemetria_in: TelemetriaCreate):
    try:
        stmt = select(Artefacto).where(
            Artefacto.mac == telemetria_in.mac_dispositivo,
            Artefacto.deleted_at.is_(None),
        )
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
            ai_status=telemetria_in.ai_status,
            estado_sin_cambios=False,
        )

        db.add(nueva_metrica)
        await db.commit()
        await db.refresh(nueva_metrica)
        nueva_metrica.mac_dispositivo = dispositivo.mac

        return nueva_metrica
    except Exception:
        await db.rollback()
        raise


async def obtener_telemetria_por_mac(db: AsyncSession, mac: str, limite: int = 50):
    stmt = (
        select(Telemetria, Artefacto.mac)
        .join(Artefacto, Telemetria.id_artefacto == Artefacto.id)
        .where(Artefacto.mac == mac, Artefacto.deleted_at.is_(None))
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


async def obtener_agregados_telemetria(
    db: AsyncSession,
    mac: str,
    granularity: str,
    desde: datetime | None,
    hasta: datetime | None,
):
    artefacto = await obtener_dispositivo_por_mac(db, mac)
    if not artefacto:
        return None

    if hasta is None:
        hasta = datetime.now(timezone.utc)
    if desde is None:
        desde = hasta - timedelta(days=1)
    if desde.tzinfo is None:
        desde = desde.replace(tzinfo=timezone.utc)
    if hasta.tzinfo is None:
        hasta = hasta.replace(tzinfo=timezone.utc)

    if granularity == "hour":
        fmt = "%Y-%m-%d %H:00:00"
        bucket_seconds = 3600
    else:
        fmt = "%Y-%m-%d 00:00:00"
        bucket_seconds = 86400

    bucket_expr = func.date_format(Telemetria.timestamp, fmt)

    stmt = (
        select(
            bucket_expr.label("bucket"),
            func.avg(Telemetria.potencia).label("potencia_promedio_w"),
            func.max(Telemetria.potencia).label("potencia_maxima_w"),
            (func.avg(Telemetria.potencia) * bucket_seconds / 3600).label("energia_wh"),
        )
        .where(
            Telemetria.id_artefacto == artefacto.id,
            Telemetria.timestamp >= desde,
            Telemetria.timestamp <= hasta,
        )
        .group_by(bucket_expr)
        .order_by(bucket_expr)
    )

    result = await db.execute(stmt)
    return result.mappings().all()


async def obtener_dispositivo_por_telemetria(db: AsyncSession, mac: str) -> Artefacto | None:
    stmt = (
        select(Artefacto)
        .where(Artefacto.mac == mac, Artefacto.deleted_at.is_(None))
        .options(selectinload(Artefacto.limites))
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# DEVICE STATE (Device Shadow)
# ---------------------------------------------------------------------------

async def actualizar_estado_deseado(db: AsyncSession, mac: str, encendido: bool) -> bool:
    try:
        stmt = select(Artefacto).where(
            Artefacto.mac == mac,
            Artefacto.deleted_at.is_(None),
        )
        result = await db.execute(stmt)
        dispositivo = result.scalar_one_or_none()

        if not dispositivo:
            return False

        dispositivo.estado_deseado = encendido
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise


async def actualizar_estado_reportado(db: AsyncSession, mac: str, encendido: bool) -> bool:
    try:
        stmt = select(Artefacto).where(
            Artefacto.mac == mac,
            Artefacto.deleted_at.is_(None),
        )
        result = await db.execute(stmt)
        dispositivo = result.scalar_one_or_none()

        if not dispositivo:
            return False

        dispositivo.estado_reportado = encendido
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise


async def actualizar_online_dispositivo(db: AsyncSession, mac: str, online: bool) -> bool:
    try:
        stmt = select(Artefacto).where(
            Artefacto.mac == mac,
            Artefacto.deleted_at.is_(None),
        )
        result = await db.execute(stmt)
        dispositivo = result.scalar_one_or_none()

        if not dispositivo:
            return False

        dispositivo.is_online = online
        dispositivo.last_seen_at = func.now()
        await db.commit()

        return True
    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------------------------
# RECOMMENDATIONS
# ---------------------------------------------------------------------------

async def crear_recomendacion_si_necesario(
    db: AsyncSession,
    id_artefacto: int,
    tipo_recomendacion: str,
    mensaje: str,
    accion_sugerida: str | None,
    severidad: str,
) -> Recomendacion | None:
    try:
        stmt = select(Recomendacion).where(
            Recomendacion.id_artefacto == id_artefacto,
            Recomendacion.tipo_recomendacion == tipo_recomendacion,
            Recomendacion.resuelto == False,
        )
        result = await db.execute(stmt)
        existente = result.scalar_one_or_none()

        if existente:
            return None

        recomendacion = Recomendacion(
            id_artefacto=id_artefacto,
            tipo_recomendacion=tipo_recomendacion,
            mensaje=mensaje,
            accion_sugerida=accion_sugerida,
            severidad=severidad,
        )
        db.add(recomendacion)
        await db.commit()
        await db.refresh(recomendacion)
        return recomendacion
    except Exception:
        await db.rollback()
        raise


async def resolver_recomendacion_auto(
    db: AsyncSession,
    id_artefacto: int,
    tipo_recomendacion: str,
) -> int:
    try:
        stmt = (
            select(Recomendacion)
            .where(
                Recomendacion.id_artefacto == id_artefacto,
                Recomendacion.tipo_recomendacion == tipo_recomendacion,
                Recomendacion.resuelto == False,
            )
        )
        result = await db.execute(stmt)
        recomendaciones = result.scalars().all()

        count = 0
        now = datetime.now(timezone.utc)
        for rec in recomendaciones:
            rec.resuelto = True
            rec.resolucion = "auto"
            rec.resuelto_en = now
            count += 1

        if count > 0:
            await db.commit()

        return count
    except Exception:
        await db.rollback()
        raise


async def obtener_recomendaciones_usuario(
    db: AsyncSession,
    user_id: int,
    solo_activas: bool = True,
) -> list[Recomendacion]:
    stmt = (
        select(Recomendacion)
        .join(Artefacto, Recomendacion.id_artefacto == Artefacto.id)
        .join(PermisoUsuarioArtefacto, Artefacto.id == PermisoUsuarioArtefacto.id_artefacto)
        .where(
            PermisoUsuarioArtefacto.id_usuario == user_id,
            Artefacto.deleted_at.is_(None),
        )
    )
    if solo_activas:
        stmt = stmt.where(Recomendacion.resuelto == False)

    stmt = stmt.order_by(Recomendacion.timestamp.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def marcar_recomendacion_resuelta(
    db: AsyncSession, recomendacion_id: int, user_id: int
) -> Recomendacion | None:
    try:
        stmt = (
            select(Recomendacion)
            .join(Artefacto, Recomendacion.id_artefacto == Artefacto.id)
            .join(PermisoUsuarioArtefacto, Artefacto.id == PermisoUsuarioArtefacto.id_artefacto)
            .where(
                Recomendacion.id == recomendacion_id,
                PermisoUsuarioArtefacto.id_usuario == user_id,
                Artefacto.deleted_at.is_(None),
            )
        )
        result = await db.execute(stmt)
        rec = result.scalar_one_or_none()

        if not rec:
            return None

        rec.resuelto = True
        rec.resolucion = "manual"
        rec.resuelto_en = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(rec)
        return rec
    except Exception:
        await db.rollback()
        raise


async def obtener_recomendaciones_resueltas_recientes(
    db: AsyncSession,
    id_artefacto: int,
    tipo_recomendacion: str,
    horas: int = 24,
) -> list[Recomendacion]:
    desde = datetime.now(timezone.utc) - timedelta(hours=horas)
    stmt = (
        select(Recomendacion)
        .where(
            Recomendacion.id_artefacto == id_artefacto,
            Recomendacion.tipo_recomendacion == tipo_recomendacion,
            Recomendacion.resuelto == True,
            Recomendacion.resuelto_en >= desde,
        )
        .order_by(Recomendacion.resuelto_en.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# USER SETTINGS
# ---------------------------------------------------------------------------

async def actualizar_settings_usuario(db: AsyncSession, user_id: int, datos: dict) -> Usuario | None:
    try:
        stmt = select(Usuario).where(Usuario.id == user_id)
        result = await db.execute(stmt)
        usuario = result.scalar_one_or_none()

        if not usuario:
            return None

        for k, v in datos.items():
            if v is not None and hasattr(usuario, k):
                setattr(usuario, k, v)

        await db.commit()
        await db.refresh(usuario)
        return usuario
    except Exception:
        await db.rollback()
        raise


async def verificar_cambio_online(db: AsyncSession, mac: str, new_state: bool) -> bool:
    stmt = select(Artefacto.is_online).where(
        Artefacto.mac == mac,
        Artefacto.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    row = result.first()

    if row is None:
        return False

    old_state = bool(row[0])
    return old_state != new_state


async def obtener_estado_dispositivo(db: AsyncSession, mac: str):
    stmt = select(Artefacto).where(
        Artefacto.mac == mac,
        Artefacto.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    dispositivo = result.scalar_one_or_none()

    if not dispositivo:
        return None

    return dispositivo.is_online


# ---------------------------------------------------------------------------
# USER SYNC
# ---------------------------------------------------------------------------

async def sincronizar_usuario(db: AsyncSession, sync_in: UserSyncRequest) -> Usuario:
    try:
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
    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------------------------
# DEVICE QUERIES
# ---------------------------------------------------------------------------

async def obtener_dispositivos_usuario(db: AsyncSession, user_id: int, prioridad: str | None = None):
    stmt = (
        select(Artefacto, PermisoUsuarioArtefacto.nivel_acceso)
        .join(PermisoUsuarioArtefacto, Artefacto.id == PermisoUsuarioArtefacto.id_artefacto)
        .where(
            PermisoUsuarioArtefacto.id_usuario == user_id,
            Artefacto.deleted_at.is_(None),
        )
        .options(selectinload(Artefacto.limites))
    )
    if prioridad is not None:
        stmt = stmt.where(Artefacto.nivel_prioridad == prioridad)
    result = await db.execute(stmt)
    return result.all()


async def obtener_dispositivo_con_acceso(db: AsyncSession, mac: str, user_id: int):
    stmt = (
        select(Artefacto, PermisoUsuarioArtefacto.nivel_acceso)
        .join(PermisoUsuarioArtefacto, Artefacto.id == PermisoUsuarioArtefacto.id_artefacto)
        .where(
            Artefacto.mac == mac,
            PermisoUsuarioArtefacto.id_usuario == user_id,
            Artefacto.deleted_at.is_(None),
        )
        .options(selectinload(Artefacto.limites))
    )
    result = await db.execute(stmt)
    return result.first()


async def verificar_acceso(db: AsyncSession, user_id: int, mac: str) -> bool:
    stmt = (
        select(PermisoUsuarioArtefacto)
        .join(Artefacto, PermisoUsuarioArtefacto.id_artefacto == Artefacto.id)
        .where(
            PermisoUsuarioArtefacto.id_usuario == user_id,
            Artefacto.mac == mac,
            Artefacto.deleted_at.is_(None),
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def obtener_dispositivo_por_mac(db: AsyncSession, mac: str) -> Artefacto | None:
    stmt = (
        select(Artefacto)
        .where(Artefacto.mac == mac, Artefacto.deleted_at.is_(None))
        .options(selectinload(Artefacto.limites))
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def crear_dispositivo(db: AsyncSession, mac: str, user_id: int) -> Artefacto:
    try:
        stmt = select(Artefacto).where(Artefacto.mac == mac)
        result = await db.execute(stmt)
        dispositivo = result.scalar_one_or_none()

        if not dispositivo:
            dispositivo = Artefacto(
                mac=mac,
                nombre_personalizado=None,
                nivel_prioridad="P2",
                is_online=False,
                estado_deseado=False,
                estado_reportado=False,
            )
            db.add(dispositivo)
            await db.flush()

            limites = ArtefactoLimite(id_artefacto=dispositivo.id, limite_consumo_w=0)
            db.add(limites)
            await db.flush()
            dispositivo.limites = limites
        elif dispositivo.deleted_at is not None:
            dispositivo.deleted_at = None
            await db.flush()
            stmt_lim = select(ArtefactoLimite).where(ArtefactoLimite.id_artefacto == dispositivo.id)
            result_lim = await db.execute(stmt_lim)
            dispositivo.limites = result_lim.scalar_one_or_none()

        stmt = select(PermisoUsuarioArtefacto).where(
            PermisoUsuarioArtefacto.id_usuario == user_id,
            PermisoUsuarioArtefacto.id_artefacto == dispositivo.id,
        )
        result = await db.execute(stmt)
        permiso_existente = result.scalar_one_or_none()

        if not permiso_existente:
            permiso = PermisoUsuarioArtefacto(
                id_usuario=user_id,
                id_artefacto=dispositivo.id,
                nivel_acceso="ADMIN",
            )
            db.add(permiso)

        await db.commit()
        await db.refresh(dispositivo)
        return dispositivo
    except Exception:
        await db.rollback()
        raise


async def actualizar_dispositivo(db: AsyncSession, mac: str, datos: dict) -> Artefacto | None:
    try:
        stmt = (
            select(Artefacto)
            .where(Artefacto.mac == mac, Artefacto.deleted_at.is_(None))
            .options(selectinload(Artefacto.limites))
        )
        result = await db.execute(stmt)
        dispositivo = result.scalar_one_or_none()

        if not dispositivo:
            return None

        limit_keys = {"limite_consumo_w", "limite_voltaje", "limite_corriente", "limite_potencia"}
        limit_datos = {}
        artefacto_datos = {}

        for k, v in datos.items():
            if k in limit_keys:
                limit_datos[k] = v
            else:
                artefacto_datos[k] = v

        if limit_datos:
            limites_obj = await _upsert_limites_artefacto_inner(db, dispositivo.id, limit_datos)
            dispositivo.limites = limites_obj

        for k, v in artefacto_datos.items():
            if v is not None and hasattr(dispositivo, k):
                setattr(dispositivo, k, v)

        await db.commit()
        await db.refresh(dispositivo)
        return dispositivo
    except Exception:
        await db.rollback()
        raise


async def eliminar_dispositivo(db: AsyncSession, mac: str) -> Artefacto | None:
    try:
        stmt = select(Artefacto).where(
            Artefacto.mac == mac,
            Artefacto.deleted_at.is_(None),
        )
        result = await db.execute(stmt)
        dispositivo = result.scalar_one_or_none()

        if not dispositivo:
            return None

        dispositivo.deleted_at = func.now()
        await db.commit()
        await db.refresh(dispositivo)
        return dispositivo
    except Exception:
        await db.rollback()
        raise


async def crear_dispositivo_o_artefacto(db: AsyncSession, mac: str) -> Artefacto:
    try:
        stmt = select(Artefacto).where(Artefacto.mac == mac)
        result = await db.execute(stmt)
        dispositivo = result.scalar_one_or_none()

        if not dispositivo:
            dispositivo = Artefacto(
                mac=mac,
                nombre_personalizado=None,
                nivel_prioridad="P2",
                is_online=False,
                estado_deseado=False,
                estado_reportado=False,
            )
            db.add(dispositivo)
            await db.flush()

            limites = ArtefactoLimite(id_artefacto=dispositivo.id, limite_consumo_w=0)
            db.add(limites)
            await db.flush()
            dispositivo.limites = limites
        elif dispositivo.deleted_at is not None:
            dispositivo.deleted_at = None
            await db.flush()
            stmt_lim = select(ArtefactoLimite).where(ArtefactoLimite.id_artefacto == dispositivo.id)
            result_lim = await db.execute(stmt_lim)
            dispositivo.limites = result_lim.scalar_one_or_none()

        stmt_users = select(Usuario).where(Usuario.auth0_id.like("%google-oauth2%"))
        result_users = await db.execute(stmt_users)
        usuarios_google = result_users.scalars().all()

        for usuario in usuarios_google:
            stmt_permiso = select(PermisoUsuarioArtefacto).where(
                PermisoUsuarioArtefacto.id_usuario == usuario.id,
                PermisoUsuarioArtefacto.id_artefacto == dispositivo.id,
            )
            result_permiso = await db.execute(stmt_permiso)
            permiso_existente = result_permiso.scalar_one_or_none()

            if not permiso_existente:
                permiso = PermisoUsuarioArtefacto(
                    id_usuario=usuario.id,
                    id_artefacto=dispositivo.id,
                    nivel_acceso="ADMIN",
                )
                db.add(permiso)

        await db.commit()
        await db.refresh(dispositivo)
        return dispositivo
    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------------------------
# LIMITS
# ---------------------------------------------------------------------------

async def obtener_limites_artefacto(db: AsyncSession, id_artefacto: int) -> ArtefactoLimite | None:
    stmt = select(ArtefactoLimite).where(ArtefactoLimite.id_artefacto == id_artefacto)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _upsert_limites_artefacto_inner(db: AsyncSession, id_artefacto: int, datos: dict) -> ArtefactoLimite:
    """Internal helper — uses flush() instead of commit(). Caller must commit."""
    stmt = select(ArtefactoLimite).where(ArtefactoLimite.id_artefacto == id_artefacto)
    result = await db.execute(stmt)
    limites = result.scalar_one_or_none()

    if not limites:
        limites = ArtefactoLimite(id_artefacto=id_artefacto)
        db.add(limites)
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            # Savepoint rolled back — outer transaction intact
            result = await db.execute(stmt)
            limites = result.scalar_one_or_none()

    for k, v in datos.items():
        if v is not None and hasattr(limites, k):
            setattr(limites, k, v)

    limites.actualizado_en = func.now()
    await db.flush()
    return limites


async def upsert_limites_artefacto(db: AsyncSession, id_artefacto: int, datos: dict) -> ArtefactoLimite:
    """Public helper — commits independently. Use _upsert_limites_artefacto_inner for nested calls."""
    try:
        limites = await _upsert_limites_artefacto_inner(db, id_artefacto, datos)
        await db.commit()
        await db.refresh(limites)
        return limites
    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------------------------
# LEASE ARBITRATION
# ---------------------------------------------------------------------------

async def activar_lease_usuario(db: AsyncSession, mac: str, duracion_minutos: int = 5) -> bool:
    try:
        stmt = select(Artefacto).where(Artefacto.mac == mac, Artefacto.deleted_at.is_(None))
        result = await db.execute(stmt)
        dispositivo = result.scalar_one_or_none()

        if not dispositivo:
            return False

        dispositivo.override_activo = True
        dispositivo.vencimiento_lease = datetime.now(timezone.utc) + timedelta(minutes=duracion_minutos)
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise


async def verificar_lease_activo(db: AsyncSession, mac: str) -> bool:
    try:
        stmt = select(Artefacto).where(
            Artefacto.mac == mac,
            Artefacto.deleted_at.is_(None),
        )
        result = await db.execute(stmt)
        dispositivo = result.scalar_one_or_none()

        if not dispositivo:
            return False

        if not dispositivo.override_activo or dispositivo.vencimiento_lease is None:
            return False

        if datetime.now(timezone.utc) >= dispositivo.vencimiento_lease:
            dispositivo.override_activo = False
            dispositivo.vencimiento_lease = None
            await db.commit()
            return False

        return True
    except Exception:
        await db.rollback()
        raise


async def romper_lease_por_seguridad(db: AsyncSession, mac: str) -> Artefacto | None:
    try:
        stmt = select(Artefacto).where(Artefacto.mac == mac, Artefacto.deleted_at.is_(None))
        result = await db.execute(stmt)
        dispositivo = result.scalar_one_or_none()

        if not dispositivo:
            return None

        dispositivo.override_activo = False
        dispositivo.vencimiento_lease = None
        await db.commit()
        await db.refresh(dispositivo)
        return dispositivo
    except Exception:
        await db.rollback()
        raise


async def cancelar_auto_kill(db: AsyncSession, mac: str, cooldown_minutes: int = 30) -> Artefacto | None:
    try:
        stmt = select(Artefacto).where(Artefacto.mac == mac, Artefacto.deleted_at.is_(None))
        result = await db.execute(stmt)
        dispositivo = result.scalar_one_or_none()

        if not dispositivo:
            return None

        dispositivo.auto_kill_at = None
        dispositivo.ai_override_until = datetime.now(timezone.utc) + timedelta(minutes=cooldown_minutes)
        await db.commit()
        await db.refresh(dispositivo)
        return dispositivo
    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------------------------
# ALERTS
# ---------------------------------------------------------------------------

async def crear_alerta_si_necesario(
    db: AsyncSession,
    id_artefacto: int,
    tipo_alerta: str,
    mensaje: str,
    severidad: str,
) -> AlertaSistema | None:
    try:
        stmt = select(AlertaSistema).where(
            AlertaSistema.id_artefacto == id_artefacto,
            AlertaSistema.tipo_alerta == tipo_alerta,
            AlertaSistema.resuelto == False,
        )
        result = await db.execute(stmt)
        alerta_existente = result.scalar_one_or_none()

        if alerta_existente:
            return None

        alerta = AlertaSistema(
            id_artefacto=id_artefacto,
            tipo_alerta=tipo_alerta,
            mensaje=mensaje,
            severidad=severidad,
        )
        db.add(alerta)
        await db.commit()
        await db.refresh(alerta)
        return alerta
    except Exception:
        await db.rollback()
        raise


async def resolver_alertas_por_tipo(
    db: AsyncSession,
    id_artefacto: int,
    tipo_alerta: str,
) -> int:
    try:
        stmt = (
            select(AlertaSistema)
            .where(
                AlertaSistema.id_artefacto == id_artefacto,
                AlertaSistema.tipo_alerta == tipo_alerta,
                AlertaSistema.resuelto == False,
            )
        )
        result = await db.execute(stmt)
        alertas = result.scalars().all()

        count = 0
        for alerta in alertas:
            alerta.resuelto = True
            count += 1

        if count > 0:
            await db.commit()

        return count
    except Exception:
        await db.rollback()
        raise


async def obtener_alertas_usuario(
    db: AsyncSession,
    user_id: int,
    solo_activas: bool = True,
) -> list[AlertaSistema]:
    stmt = (
        select(AlertaSistema)
        .join(Artefacto, AlertaSistema.id_artefacto == Artefacto.id)
        .join(PermisoUsuarioArtefacto, Artefacto.id == PermisoUsuarioArtefacto.id_artefacto)
        .where(
            PermisoUsuarioArtefacto.id_usuario == user_id,
            Artefacto.deleted_at.is_(None),
        )
    )
    if solo_activas:
        stmt = stmt.where(AlertaSistema.resuelto == False)

    stmt = stmt.order_by(AlertaSistema.timestamp.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def marcar_alerta_resuelta(db: AsyncSession, alerta_id: int, user_id: int) -> AlertaSistema | None:
    try:
        stmt = (
            select(AlertaSistema)
            .join(Artefacto, AlertaSistema.id_artefacto == Artefacto.id)
            .join(PermisoUsuarioArtefacto, Artefacto.id == PermisoUsuarioArtefacto.id_artefacto)
            .where(
                AlertaSistema.id == alerta_id,
                PermisoUsuarioArtefacto.id_usuario == user_id,
                Artefacto.deleted_at.is_(None),
            )
        )
        result = await db.execute(stmt)
        alerta = result.scalar_one_or_none()

        if not alerta:
            return None

        alerta.resuelto = True
        await db.commit()
        await db.refresh(alerta)
        return alerta
    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------------------------
# EVENTS
# ---------------------------------------------------------------------------

async def crear_evento(
    db: AsyncSession,
    id_artefacto: int,
    accion: str,
    razon_disparo: str | None = None,
    id_usuario: int | None = None,
) -> EventoUsuario:
    try:
        evento = EventoUsuario(
            id_artefacto=id_artefacto,
            id_usuario=id_usuario,
            accion=accion,
            razon_disparo=razon_disparo,
        )
        db.add(evento)
        await db.commit()
        await db.refresh(evento)
        return evento
    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------------------------
# COMPOSITE OPERATIONS (single-transaction)
# ---------------------------------------------------------------------------

async def comando_estado_con_lease(
    db: AsyncSession,
    mac: str,
    encendido: bool,
    duracion_minutos: int = 5,
    id_usuario: int | None = None,
) -> Artefacto | None:
    """Set estado_deseado + activate lease + log event in a single transaction."""
    try:
        stmt = select(Artefacto).where(Artefacto.mac == mac, Artefacto.deleted_at.is_(None))
        result = await db.execute(stmt)
        dispositivo = result.scalar_one_or_none()

        if not dispositivo:
            return None

        dispositivo.estado_deseado = encendido
        dispositivo.override_activo = True
        dispositivo.vencimiento_lease = datetime.now(timezone.utc) + timedelta(minutes=duracion_minutos)
        await db.flush()

        evento = EventoUsuario(
            id_artefacto=dispositivo.id,
            id_usuario=id_usuario,
            accion="comando_estado",
            razon_disparo=f"Relay {'encendido' if encendido else 'apagado'} por usuario (lease activado)",
        )
        db.add(evento)

        await db.commit()
        await db.refresh(dispositivo)
        return dispositivo
    except Exception:
        await db.rollback()
        raise


async def obtener_eventos_usuario(
    db: AsyncSession,
    user_id: int,
    mac: str | None = None,
    limite: int = 50,
) -> list[EventoUsuario]:
    stmt = (
        select(EventoUsuario)
        .join(Artefacto, EventoUsuario.id_artefacto == Artefacto.id)
        .join(PermisoUsuarioArtefacto, Artefacto.id == PermisoUsuarioArtefacto.id_artefacto)
        .where(
            PermisoUsuarioArtefacto.id_usuario == user_id,
            Artefacto.deleted_at.is_(None),
        )
    )

    if mac is not None:
        stmt = stmt.where(Artefacto.mac == mac)

    stmt = stmt.order_by(EventoUsuario.timestamp.desc()).limit(limite)
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# EMERGENCY BMS SHUTDOWN
# ---------------------------------------------------------------------------

async def emergencia_bms_shutdown(db: AsyncSession, mac: str, alerta_msg: str, ai_status: int) -> Artefacto | None:
    try:
        stmt = select(Artefacto).where(Artefacto.mac == mac, Artefacto.deleted_at.is_(None))
        result = await db.execute(stmt)
        dispositivo = result.scalar_one_or_none()

        if not dispositivo:
            return None

        dispositivo.estado_deseado = False
        dispositivo.estado_reportado = False
        dispositivo.override_activo = False
        dispositivo.vencimiento_lease = None

        await db.flush()

        stmt_alerta = select(AlertaSistema).where(
            AlertaSistema.id_artefacto == dispositivo.id,
            AlertaSistema.tipo_alerta == "bms_critica",
            AlertaSistema.resuelto == False,
        )
        result_alerta = await db.execute(stmt_alerta)
        alerta_existente = result_alerta.scalar_one_or_none()

        if not alerta_existente:
            alerta = AlertaSistema(
                id_artefacto=dispositivo.id,
                tipo_alerta="bms_critica",
                mensaje=f"Alerta BMS: {alerta_msg} (AI Status: {ai_status})",
                severidad="critica",
            )
            db.add(alerta)

        evento = EventoUsuario(
            id_artefacto=dispositivo.id,
            accion="safety_override",
            razon_disparo=f"Apagado de emergencia BMS: {alerta_msg} (AI Status: {ai_status})",
        )
        db.add(evento)

        await db.commit()
        await db.refresh(dispositivo)
        return dispositivo
    except Exception:
        await db.rollback()
        raise