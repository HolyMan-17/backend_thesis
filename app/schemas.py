from pydantic import BaseModel, Field, field_validator
from datetime import datetime, timezone
from typing import Optional
import re

PRIORIDAD_VALIDA = {"P1", "P2", "P3"}


def _serialize_datetime(v: datetime) -> str:
    if v.tzinfo is None:
        return v.isoformat() + "Z"
    return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


MAC_REGEX = r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$"


# --- TELEMETRY SCHEMAS ---
class TelemetriaBase(BaseModel):
    voltaje: float = Field(..., ge=0)
    corriente: float = Field(..., ge=0)
    potencia: float = Field(..., ge=0)
    tiempo_operacion_s: int = Field(default=0, ge=0)
    ai_status: int = Field(default=0, ge=0, le=2)


class TelemetriaCreate(TelemetriaBase):
    mac_dispositivo: str = Field(..., min_length=17, max_length=17, pattern=MAC_REGEX)


class TelemetriaResponse(TelemetriaBase):
    id: int
    mac_dispositivo: str
    timestamp: datetime
    estado_sin_cambios: bool

    class Config:
        from_attributes = True
        json_encoders = {datetime: _serialize_datetime}


# --- DEVICE SCHEMAS ---
class DispositivoCreate(BaseModel):
    mac: str = Field(..., min_length=17, max_length=17, pattern=MAC_REGEX)


class DispositivoUpdate(BaseModel):
    nombre_personalizado: Optional[str] = None
    nivel_prioridad: Optional[str] = None
    limite_consumo_w: Optional[float] = Field(default=None, ge=0)
    limite_voltaje: Optional[float] = Field(default=None, ge=0.1, le=60.0)
    limite_corriente: Optional[float] = Field(default=None, ge=0.1, le=30.0)
    limite_potencia: Optional[float] = Field(default=None, ge=0.1, le=500.0)

    @field_validator('nombre_personalizado')
    @classmethod
    def validate_nombre(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if v == '':
                raise ValueError('El nombre no puede estar vacío')
        return v

    @field_validator('nivel_prioridad')
    @classmethod
    def validate_nivel_prioridad(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in PRIORIDAD_VALIDA:
            raise ValueError(f'nivel_prioridad debe ser uno de {sorted(PRIORIDAD_VALIDA)}')
        return v


class DispositivoResponse(BaseModel):
    id: int
    mac: str
    nombre_personalizado: Optional[str] = None
    nivel_prioridad: str
    limite_consumo_w: float
    limite_voltaje: Optional[float] = None
    limite_corriente: Optional[float] = None
    limite_potencia: Optional[float] = None
    estado_deseado: bool
    estado_reportado: bool
    is_online: bool
    nivel_acceso: str = "ADMIN"
    last_seen_at: Optional[datetime] = None

    class Config:
        from_attributes = True
        json_encoders = {datetime: _serialize_datetime}


# --- COMMAND SCHEMAS ---
class ComandoEstado(BaseModel):
    encendido: bool


class ComandoLimites(BaseModel):
    limite_consumo_w: Optional[float] = Field(default=None, ge=0)
    limite_voltaje: Optional[float] = Field(default=None, ge=0.1, le=60.0)
    limite_corriente: Optional[float] = Field(default=None, ge=0.1, le=30.0)
    limite_potencia: Optional[float] = Field(default=None, ge=0.1, le=500.0)


# --- USER SYNC SCHEMA ---
class UserSyncRequest(BaseModel):
    auth0_id: str
    email: str
    nombre: Optional[str] = None


# --- ALERT SCHEMAS ---
class AlertaResponse(BaseModel):
    id: int
    id_artefacto: int
    tipo_alerta: str
    mensaje: str
    severidad: str
    leido: bool
    resuelto: bool
    timestamp: datetime

    class Config:
        from_attributes = True
        json_encoders = {datetime: _serialize_datetime}


class AlertaUpdate(BaseModel):
    resuelto: bool


# --- EVENT SCHEMAS ---
class EventoResponse(BaseModel):
    id: int
    id_artefacto: int
    id_usuario: Optional[int] = None
    accion: str
    razon_disparo: Optional[str] = None
    timestamp: datetime

    class Config:
        from_attributes = True
        json_encoders = {datetime: _serialize_datetime}


# --- AGGREGATE SCHEMAS ---
class AgregadoResponse(BaseModel):
    bucket: datetime
    potencia_promedio_w: float
    potencia_maxima_w: float
    energia_wh: float

    class Config:
        json_encoders = {datetime: _serialize_datetime}


class AgregadoQuery(BaseModel):
    granularity: str = Field(default="hour", pattern=r"^(hour|day)$")
    desde: Optional[datetime] = None
    hasta: Optional[datetime] = None


# --- LEGACY SCHEMAS (kept for existing endpoints until Phase 7 migration) ---
class DispositivoEstado(BaseModel):
    mac_dispositivo: str
    encendido: bool


class DispositivoLimites(BaseModel):
    mac_dispositivo: str
    limite_consumo_w: Optional[float] = None
    limite_voltaje: Optional[float] = None
    limite_corriente: Optional[float] = None
    limite_potencia: Optional[float] = None


class DispositivoEstadoResponse(BaseModel):
    mac_dispositivo: str
    is_online: bool
