from pydantic import BaseModel, Field
from datetime import datetime, timezone
from typing import Optional
import re


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


class DispositivoResponse(BaseModel):
    id: int
    mac: str
    nombre_personalizado: Optional[str] = None
    nivel_prioridad: str
    limite_consumo_w: float
    is_online: bool
    is_encendido: bool
    nivel_acceso: str = "ADMIN"
    last_seen_at: Optional[datetime] = None

    class Config:
        from_attributes = True
        json_encoders = {datetime: _serialize_datetime}


# --- COMMAND SCHEMAS ---
class ComandoEstado(BaseModel):
    encendido: bool


class ComandoLimites(BaseModel):
    limite_voltaje: Optional[float] = Field(default=None, ge=0.1, le=60.0)
    limite_corriente: Optional[float] = Field(default=None, ge=0.1, le=30.0)
    limite_potencia: Optional[float] = Field(default=None, ge=0.1, le=500.0)


# --- USER SYNC SCHEMA ---
class UserSyncRequest(BaseModel):
    auth0_id: str
    email: str
    nombre: Optional[str] = None


# --- LEGACY SCHEMAS (kept for existing endpoints until Phase 7 migration) ---
class DispositivoEstado(BaseModel):
    mac_dispositivo: str
    encendido: bool


class DispositivoLimites(BaseModel):
    mac_dispositivo: str
    limite_voltaje: Optional[float] = None
    limite_corriente: Optional[float] = None
    limite_potencia: Optional[float] = None


class DispositivoEstadoResponse(BaseModel):
    mac_dispositivo: str
    is_online: bool