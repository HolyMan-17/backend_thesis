# External Integrations

**Analysis Date:** 2026-05-12

## APIs & External Services

**No external REST/HTTP APIs are consumed.** The application is purely a backend gateway between MQTT devices and a MariaDB database. There are no outbound HTTP calls to third-party services.

**The application EXPOSES a REST API** via FastAPI:

| Method | Endpoint | Purpose | File |
|--------|----------|---------|------|
| GET | `/health` | Liveness probe (returns `{}`) | `app/main.py:31-34` |
| GET | `/api/telemetria/{mac_dispositivo}` | Read telemetry for a device | `app/main.py:36-39` |
| POST | `/api/telemetria` | Register telemetry from ESP32 | `app/main.py:41-46` |
| POST | `/api/comando/estado` | Toggle device relay on/off + publish MQTT command | `app/main.py:48-60` |
| POST | `/api/comando/limites` | Update device limits + publish MQTT command | `app/main.py:62-78` |
| GET | `/api/dispositivos/{mac_dispositivo}/estado` | Get device online status | `app/main.py:80-86` |

## MQTT Broker (Mosquitto)

**The primary integration in this system.** The application communicates with a local Mosquitto MQTT broker at `127.0.0.1:1883`.

### MQTT Subscriber (Inbound)

**Purpose:** Listen for device telemetry and connection status messages.

- **Implementation:** `app/mqtt_listener.py`
- **Client ID:** `FastAPI_Consumidor_{pid}`
- **Auth:** Username/password from env vars `MQTT_USER` / `MQTT_PASS` (with hardcoded fallbacks at lines 13-14)
- **Subscribed topics:**
  - `smartups/dispositivos/+/telemetria` — Device telemetry readings
  - `smartups/dispositivos/+/conexion` — Device birth/LWT messages

- **Topic parsing:** `smartups/dispositivos/{MAC}/{type}` — MAC extracted from `partes_topic[2]`, type from `partes_topic[3]`
- **Telemetry flow:** MQTT message → `procesar_payload()` → `TelemetriaCreate` schema → `crear_telemetria()` → MariaDB
- **Connection status flow:** MQTT message → `procesar_payload()` → `actualizar_estado_dispositivo()` → MariaDB (sets `is_encendido` — **known semantic bug**, should set `is_online`)

- **Thread model:** `client.loop_start()` spawns a background thread; `asyncio.run_coroutine_threadsafe()` bridges to the FastAPI event loop stored in `_main_loop` global

- **Startup:** Called from `app/main.py:24` inside the FastAPI lifespan context manager
- **Shutdown:** `client.loop_stop()` + `client.disconnect()` in lifespan teardown (`app/main.py:26-27`)

### MQTT Publisher (Outbound)

**Purpose:** Send commands from the REST API to ESP32 devices.

- **Implementation:** `paho.mqtt.publish.single()` in `app/main.py:57,75`
- **Auth:** **Hardcoded** credentials `esp-gateway` / `wUbcJJiZcLqV3dDo2r9e` (lines 56, 74)
- **Hostname:** `127.0.0.1`
- **QoS:** Default (0)
- **Published topics:**
  - `smartups/dispositivos/{MAC}/comando/estado` — Relay on/off command with payload `{"encendido": true/false}`
  - `smartups/dispositivos/{MAC}/comando/limites` — Limit update command with partially-serialized schema payload

### MQTT Simulator (Development Tool)

- **Implementation:** `app/mock_esp32.py`
- **Client ID:** `esp32_simulator_1`
- **Auth:** Hardcoded `esp-gateway` / `wUbcJJiZcLqV3dDo2r9e`
- **Publishes to:** `smartups/dispositivos/{MAC}/telemetria` (every 5 seconds)
- **Subscribes to:** `smartups/dispositivos/{MAC}/comando/#`
- **LWT:** Last Will and Testament published to `smartups/dispositivos/{MAC}/conexion` with `{"is_online": false}`
- **Birth message:** Published to same topic with `{"is_online": true}`

## Data Storage

**Databases:**

- **MariaDB** (InnoDB engine)
  - Connection: `mysql+aiomysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4`
  - Connection string constructed in: `app/database.py:22`
  - ORM: SQLAlchemy 2.0 async (`AsyncSession`, `declarative_base`)
  - Session factory: `AsyncSessionLocal` in `app/database.py:36-42`
  - Dependency injection: `get_db()` in `app/database.py:48-57`
  - Engine config: `pool_pre_ping=True`, `pool_recycle=3600`, `echo=False` (`app/database.py:27-32`)
  - Schema DDL: `schema_iot.sql` (110 lines)
  - **Range partitioning** on `telemetria` table by month (through 2026-12 + `p_max` catch-all)

  **Tables** (all use Spanish naming):
  | Table | Purpose | ORM Model | File |
  |-------|---------|-----------|------|
  | `app_api_keys` | Mobile app API key authentication | `AppApiKey` | `app/models.py:6-13` |
  | `artefactos` | Device registry + device shadow | `Artefacto` | `app/models.py:18-45` |
  | `permisos_app_artefacto` | ACL junction: app↔device access | `PermisoAppArtefacto` | `app/models.py:47-56` |
  | `alertas_sistema` | System alert log | `AlertaSistema` | `app/models.py:58-69` |
  | `credenciales_mtls` | mTLS certificate credentials | `CredencialMtls` | `app/models.py:71-81` |
  | `despliegues_ota` | OTA firmware/deployment tracking | `DespliegueOta` | `app/models.py:83-94` |
  | `eventos_usuario` | User event/action log | `EventoUsuario` | `app/models.py:96-105` |
  | `telemetria` | High-frequency time-series data (partitioned) | `Telemetria` | `app/models.py:107-119` |

  **Notable constraint:** `telemetria.id_artefacto` has **no foreign key** due to MariaDB partitioning limitations (`app/models.py:111-112`).

**File Storage:**
- Local filesystem only — no S3, GCS, or other object storage integration

**Caching:**
- None — no Redis, Memcached, or in-memory caching layer detected

## Authentication & Identity

**Auth Provider:**
- **Custom / API Key** — The `app_api_keys` table and `PermisoAppArtefacto` model define an API key + ACL system, but **no authentication middleware is currently enforced** in FastAPI. The models exist in the schema but are not used in any endpoint.
- **No authentication decorators, dependencies, or middleware** are applied to any route in `app/main.py`.

**MQTT Auth:**
- Mosquitto broker requires username/password authentication
- Subscriber (`app/mqtt_listener.py:13-14`): reads `MQTT_USER`/`MQTT_PASS` from env vars with hardcoded fallback defaults
- Publisher (`app/main.py:56,74`): **hardcoded credentials** — security concern (see below)

## Monitoring & Observability

**Error Tracking:**
- None — no Sentry, Rollbar, or similar integration

**Logs:**
- `print()` statements with emoji prefixes in `app/mqtt_listener.py` (lines 18, 22, 50, 54, 65, 69)
- `logging.getLogger("uvicorn.error")` in `app/main.py:20` — logger created but never used
- Uvicorn default request logging to stdout/stderr

**Health Check:**
- `GET /health` returns `{}` — basic liveness probe, no dependency checks (`app/main.py:31-34`)

## CI/CD & Deployment

**Hosting:**
- Linux VPS (implied by documentation references)

**CI Pipeline:**
- **None detected** — no CI/CD configuration files (`.github/`, `.gitlab-ci.yml`, `Jenkinsfile`, etc.)

**Containerization:**
- **None detected** — no `Dockerfile`, `docker-compose.yml`, or Kubernetes manifests

## Environment Configuration

**Required env vars** (loaded from `/home/manu0ak/iot_backend/.env`):
- `DB_USER` — MariaDB username (required, validated at startup)
- `DB_PASSWORD` — MariaDB password (required, validated at startup)
- `DB_NAME` — MariaDB database name (required, validated at startup)
- `DB_HOST` — MariaDB hostname (used in connection string, no validation)
- `DB_PORT` — MariaDB port (used in connection string, no validation)
- `MQTT_USER` — MQTT broker username (optional, defaults to `"esp-gateway"`)
- `MQTT_PASS` — MQTT broker password (optional, defaults to hardcoded value)

**Secrets location:**
- `.env` file at project root — contains database credentials and defaults for MQTT auth

**Security note:** `app/database.py:17-18` validates presence of `DB_USER`, `DB_PASSWORD`, `DB_NAME` but does **not** validate `DB_HOST` or `DB_PORT` (which default to `None` and would produce a malformed connection string).

## Webhooks & Callbacks

**Incoming:**
- None — the application has no webhook receiver endpoints

**Outgoing:**
- MQTT publish commands to ESP32 devices (described above in MQTT Publisher section)

## Integration Architecture Diagram

```text
┌────────────────┐       MQTT Pub        ┌─────────────────────┐
│   ESP32 Device │◄──────────────────────┤                     │
│   (or mock)    │────── MQTT Sub ───────►│   Mosquitto Broker  │
└────────────────┘   telemetria/conexion  │   127.0.0.1:1883    │
                                           └─────────┬───────────┘
                                                     │ MQTT Sub (background thread)
                                                     ▼
                                           ┌─────────────────────┐
                                           │  app/mqtt_listener  │
                                           │  (paho-mqtt client) │
                                           └─────────┬───────────┘
                                                     │ asyncio bridge
                                                     ▼
┌────────────────┐       HTTP            ┌─────────────────────┐       Async SQL        ┌─────────┐
│  Mobile App /  │────── POST/GET ──────►│    FastAPI / Uvicorn │──────────────────────►│ MariaDB │
│  Frontend      │   /api/* endpoints    │    app/main.py       │   sqlalchemy+aiomysql │         │
└────────────────┘                       └─────────────────────┘                        └─────────┘
```

## Known Integration Issues

1. **Hardcoded MQTT credentials in publisher:** `app/main.py:56,74` hardcodes `esp-gateway` / `wUbcJJiZcLqV3dDo2r9e`. The subscriber correctly reads from env vars (`app/mqtt_listener.py:13-14`). These should be unified.

2. **Semantic bug — conexion handler:** `app/mqtt_listener.py:63` calls `actualizar_estado_dispositivo(db, mac, encendido=estado_bool)` which sets `is_encendido` (relay state) when the intent from MQTT birth/LWT is to set `is_online` (network reachability). See `AGENTS.md` for details.

3. **No database foreign key on telemetria:** `telemetria.id_artefacto` lacks a foreign key constraint due to MariaDB partitioning limitations (`app/models.py:111-112`, `schema_iot.sql:89-110`). Invalid `id_artefacto` values could be inserted.

4. **Synchronous MQTT publish in async context:** `app/main.py:57,75` uses `paho.mqtt.publish.single()` which is a blocking call inside an async endpoint — can block the event loop under load.

---

*Integration audit: 2026-05-12*