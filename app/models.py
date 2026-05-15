from sqlalchemy import Column, Integer, BigInteger, String, Boolean, Numeric, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


# --- DEPRECATED: Use Usuario + PermisoUsuarioArtefacto instead ---
# class AppApiKey(Base):
#     __tablename__ = 'app_api_keys'
#     ...kept for backward compatibility during migration


class Usuario(Base):
    __tablename__ = 'usuarios'

    id = Column(Integer, primary_key=True, autoincrement=True)
    auth0_id = Column(String(255), unique=True, nullable=False)
    email = Column(String(255), nullable=False)
    nombre = Column(String(255))
    fecha_registro = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    ultimo_acceso = Column(DateTime(timezone=True), nullable=True)
    activo = Column(Boolean, default=True, nullable=False)

    permisos = relationship("PermisoUsuarioArtefacto", back_populates="usuario", cascade="all, delete-orphan")


class Artefacto(Base):
    __tablename__ = 'artefactos'

    id = Column(Integer, primary_key=True, autoincrement=True)
    mac = Column(String(17), unique=True, nullable=False)
    nombre_personalizado = Column(String(100))
    nivel_prioridad = Column(String(10), nullable=False)
    limite_consumo_w = Column(Numeric(8, 2), nullable=False)

    # Device Shadow
    estado_deseado = Column(Boolean, default=False, nullable=False)
    estado_reportado = Column(Boolean, default=False, nullable=False)

    # Liveness
    is_online = Column(Boolean, default=False, nullable=False, index=True)
    is_encendido = Column(Boolean, default=False, server_default="0", nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    # Lease Mechanism
    override_activo = Column(Boolean, default=False, nullable=False)
    vencimiento_lease = Column(DateTime(timezone=True), nullable=True)

    # Cascading Relationships
    permisos_usuario = relationship("PermisoUsuarioArtefacto", back_populates="artefacto", cascade="all, delete-orphan")
    alertas = relationship("AlertaSistema", back_populates="artefacto", cascade="all, delete-orphan")
    credenciales = relationship("CredencialMtls", back_populates="artefacto", cascade="all, delete-orphan")
    despliegues = relationship("DespliegueOta", back_populates="artefacto", cascade="all, delete-orphan")
    eventos = relationship("EventoUsuario", back_populates="artefacto", cascade="all, delete-orphan")


class PermisoUsuarioArtefacto(Base):
    __tablename__ = 'permisos_usuario_artefacto'

    id_usuario = Column(Integer, ForeignKey('usuarios.id', ondelete="CASCADE"), primary_key=True)
    id_artefacto = Column(Integer, ForeignKey('artefactos.id', ondelete="CASCADE"), primary_key=True)
    nivel_acceso = Column(String(20), default='ADMIN', nullable=False)
    fecha_asignacion = Column(DateTime(timezone=True), default=func.now(), nullable=False)

    usuario = relationship("Usuario", back_populates="permisos")
    artefacto = relationship("Artefacto", back_populates="permisos_usuario")


# --- DEPRECATED: Use PermisoUsuarioArtefacto instead ---
# class PermisoAppArtefacto(Base):
#     __tablename__ = 'permisos_app_artefacto'
#     ...kept for backward compatibility during migration


class AlertaSistema(Base):
    __tablename__ = 'alertas_sistema'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    id_artefacto = Column(Integer, ForeignKey('artefactos.id', ondelete="CASCADE"), nullable=False, index=True)
    tipo_alerta = Column(String(50), nullable=False)
    mensaje = Column(String(255), nullable=False)
    severidad = Column(String(20), nullable=False)
    leido = Column(Boolean, default=False, nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), default=func.now(), nullable=False)

    artefacto = relationship("Artefacto", back_populates="alertas")


class CredencialMtls(Base):
    __tablename__ = 'credenciales_mtls'

    id = Column(Integer, primary_key=True, autoincrement=True)
    id_artefacto = Column(Integer, ForeignKey('artefactos.id', ondelete="CASCADE"), nullable=False)
    hash_certificado = Column(String(255), nullable=False)
    token_activo = Column(String(255), nullable=False)
    fecha_emision = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    estado_revocado = Column(Boolean, default=False, nullable=False)

    artefacto = relationship("Artefacto", back_populates="credenciales")


class DespliegueOta(Base):
    __tablename__ = 'despliegues_ota'

    id = Column(Integer, primary_key=True, autoincrement=True)
    id_artefacto = Column(Integer, ForeignKey('artefactos.id', ondelete="CASCADE"), nullable=False)
    version_modelo_ml = Column(String(50), nullable=False)
    url_descarga = Column(String(255), nullable=False)
    hash_firma = Column(String(255), nullable=False)
    estado_despliegue = Column(String(50), nullable=False)
    fecha_despliegue = Column(DateTime(timezone=True), default=func.now(), nullable=False)

    artefacto = relationship("Artefacto", back_populates="despliegues")


class EventoUsuario(Base):
    __tablename__ = 'eventos_usuario'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    id_artefacto = Column(Integer, ForeignKey('artefactos.id', ondelete="CASCADE"), nullable=False)
    id_usuario = Column(Integer, ForeignKey('usuarios.id', ondelete="SET NULL"), nullable=True)
    accion = Column(String(100), nullable=False)
    razon_disparo = Column(String(255))
    timestamp = Column(DateTime(timezone=True), default=func.now(), nullable=False)

    artefacto = relationship("Artefacto", back_populates="eventos")
    usuario = relationship("Usuario")


class Telemetria(Base):
    __tablename__ = 'telemetria'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # CRITICAL: No ForeignKey applied here due to MariaDB partitioning constraints
    id_artefacto = Column(Integer, nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), primary_key=True, default=func.now(), nullable=False)
    voltaje = Column(Numeric(8, 2), nullable=False)
    corriente = Column(Numeric(8, 2), nullable=False)
    potencia = Column(Numeric(8, 2), nullable=False)
    tiempo_operacion_s = Column(Integer, nullable=False)
    estado_sin_cambios = Column(Boolean, default=False, nullable=False)