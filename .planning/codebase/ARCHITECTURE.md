<!-- refreshed: 2026-05-12 -->
# Architecture

**Analysis Date:** 2026-05-12

## System Overview

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                        External Clients                                 │
├──────────────────────────┬─────────────────────────────────────────────┤
│   Mobile App (REST API)  │        ESP32 Devices (MQTT)                │
│   GET/POST JSON endpoints│   Pub: .../telemetria, .../conexion        │
│                          │   Sub: .../comando/estado, .../comando/limites│
└──────────┬───────────────┴────────────┬────────────────────────────────┘
           │                            │
           ▼                            ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     FastAPI Application                                 │
│                         `app/main.py`                                   │
│                                                                         │
│  ┌──────────────────┐  ┌──────────────────────────────────────────┐    │
│  │  REST Endpoints   │  │  MQTT Listener (background thread)      │    │
│  │  /api/telemetria  │  │  `app/mqtt_listener.py`                 │    │
│  │  /api/comando/*   │  │                                         │    │
│  │  /api/dispositivos│  │  on_message ──► procesar_payload()      │    │
│  │  /health          │  │       │                                 │    │
│  └────────┬─────────┘  └───────┼─────────────────────────────────┘    │
│           │                     │                                       │
│           ▼                     ▼                                       │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                      CRUD Layer                                  │  │
│  │                    `app/crud.py`                                  │  │
│  │  crear_telemetria · obtener_telemetria_por_mac                   │  │
│  │  actualizar_estado_dispositivo · obtener_estado_dispositivo      │  │
│  └──────────────────────────┬───────────────────────────────────────┘  │
│                              │                                          │
│           ┌──────────────────┼──────────────────┐                     │
│           ▼                  ▼                  ▼                     │
│  ┌──────────────┐  ┌────────────────┐  ┌──────────────────────┐       │
│  │   Schemas    │  │    Models      │  │   Database Engine     │       │
│  │ `schemas.py` │  │ `models.py`    │  │  `database.py`        │       │
│  └──────────────┘  └────────────────┘  └──────────────────────┘       │
└─────────────────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     MariaDB (Partitioned)                               │
│  artefactos │ telemetria (range-partitioned by month) │ app_api_keys   │
│  permisos_app_artefacto │ alertas_sistema │ credenciales_mtls          │
│  despliegues_ota │ eventos_usuario                                        │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                  Mosquitto MQTT Broker (127.0.0.1:1883)                │
│  Topic tree: smartups/dispositivos/{mac}/telemetria                    │
│              smartups/dispositivos/{mac}/conexion                       │
│              smartups/dispositivos/{mac}/comando/estado                 │
│              smartups/dispositivos/{mac}/comando/limites                │
└─────────────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| FastAPI App | HTTP endpoint routing, request validation, MQTT publish, lifespan management | `app/main.py` |
| MQTT Listener | Subscribe to device topics, parse payloads, dispatch to CRUD layer | `app/mqtt_listener.py` |
| CRUD Layer | Async database read/write operations — all DB access goes through here | `app/crud.py` |
| Models | SQLAlchemy ORM table definitions with relationships and constraints | `app/models.py` |
| Schemas | Pydantic request/response models with validation rules | `app/schemas.py` |
| Database | Async engine + session factory + dependency injection for DB sessions | `app/database.py` |
| ESP32 Simulator | Standalone script that publishes fake telemetry and listens for commands | `app/mock_esp32.py` |
| Schema DDL | MariaDB DDL with partitioning — bootstrap once | `schema_iot.sql` |

## Pattern Overview

**Overall:** Layered monolith with dual-protocol ingress (REST + MQTT)

**Key Characteristics:**
- Single-process Python application using async I/O (FastAPI + aiomysql)
- MQTT listener runs in a background thread managed by FastAPI lifespan; bridge pattern crosses thread↔async boundary via `asyncio.run_coroutine_threadsafe()`
- All code is co-located in flat `app/` package — no layered subdirectories
- Spanish naming convention for models, tables, columns, and REST paths
- MariaDB time-series partitioning on `telemetria` table (range by month)
- No authentication or authorization enforced on REST endpoints
- No `__init__.py` — all imports use `app.` package prefix resolved from project root

## Layers

**HTTP / Presentation Layer:**
- Purpose: Expose REST API endpoints, validate incoming requests with Pydantic schemas, publish MQTT commands
- Location: `app/main.py`
- Contains: 5 FastAPI route functions + 1 lifespan context manager
- Depends on: `app/crud.py`, `app/schemas.py`, `app/database.py`, `paho.mqtt.publish`
- Used by: Mobile app / external HTTP clients

**MQTT / Ingestion Layer:**
- Purpose: Consume MQTT messages from ESP32 devices, parse topic/payload, dispatch to async DB operations
- Location: `app/mqtt_listener.py`
- Contains: MQTT `on_connect`, `on_message` callbacks, `procesar_payload()` async handler, `iniciar_oyente_mqtt()` init
- Depends on: `app/database.py` (AsyncSessionLocal), `app/crud.py`, `app/schemas.py`
- Used by: FastAPI lifespan (started as background thread)

**Data Access Layer:**
- Purpose: Encapsulate all SQLAlchemy queries — create, read, update operations
- Location: `app/crud.py`
- Contains: 4 async functions (`crear_telemetria`, `obtener_telemetria_por_mac`, `actualizar_estado_dispositivo`, `obtener_estado_dispositivo`)
- Depends on: `app/models.py`, `app/schemas.py`, SQLAlchemy AsyncSession
- Used by: Both `main.py` (REST) and `mqtt_listener.py` (MQTT)

**Data Model Layer:**
- Purpose: Define ORM models with columns, types, relationships, and table names
- Location: `app/models.py`
- Contains: 8 model classes (Artefacto, Telemetria, AppApiKey, PermisoAppArtefacto, AlertaSistema, CredencialMtls, DespliegueOta, EventoUsuario)
- Depends on: `app/database.py` (Base class)
- Used by: `app/crud.py`

**Schema / Validation Layer:**
- Purpose: Pydantic models for request deserialization and response serialization
- Location: `app/schemas.py`
- Contains: 5 model classes (TelemetriaBase, TelemetriaCreate, TelemetriaResponse, DispositivoEstado, DispositivoLimites, DispositivoEstadoResponse)
- Depends on: pydantic (standalone)
- Used by: `app/main.py` (endpoint signatures), `app/mqtt_listener.py` (payload parsing)

**Infrastructure Layer:**
- Purpose: Database engine creation, session factory, dependency injection
- Location: `app/database.py`
- Contains: `create_async_engine`, `AsyncSessionLocal`, `Base`, `get_db()` async generator
- Depends on: `.env` file (DB credentials)
- Used by: All layers requiring DB access

## Data Flow

### Primary Request Path: Telemetry Ingestion (MQTT → DB)

1. ESP32 publishes to `smartups/dispositivos/{mac}/telemetria` (`app/mock_esp32.py:68`)
2. Mosquitto broker delivers message to `on_message` callback (`app/mqtt_listener.py:24`)
3. `on_message` schedules `procesar_payload` on the async event loop (`app/mqtt_listener.py:30`)
4. `procesar_payload` extracts MAC from topic, parses JSON payload (`app/mqtt_listener.py:32-42`)
5. Payload is validated and deserialized into `TelemetriaCreate` schema (`app/mqtt_listener.py:47`)
6. MAC in payload is cross-checked against topic MAC (`app/mqtt_listener.py:49`)
7. `crear_telemetria` resolves MAC → `Artefacto.id`, creates `Telemetria` row, commits (`app/crud.py:7-29`)
8. Database persists row in the appropriate monthly partition

### Device Command Path (REST → MQTT → ESP32)

1. Mobile app sends `POST /api/comando/estado` with `{mac_dispositivo, encendido}` (`app/main.py:48-60`)
2. Endpoint updates `Artefacto.is_encendido` in DB via `actualizar_estado_dispositivo` (`app/crud.py:42-55`)
3. Endpoint publishes command to `smartups/dispositivos/{mac}/comando/estado` via `paho.mqtt.publish.single` (`app/main.py:54-57`)
4. ESP32 subscribes to `smartups/dispositivos/{mac}/comando/#` and processes command (`app/mock_esp32.py:15-36`)

### Device Liveness Path (MQTT Birth/LWT → DB)

1. ESP32 connects and publishes birth message `{"is_online": true}` to `.../conexion` (`app/mock_esp32.py:47-48`)
2. ESP32 sets Last Will `{"is_online": false}` on same topic (`app/mock_esp32.py:44-45`)
3. `on_message` receives `conexion` message and calls `actualizar_estado_dispositivo(db, mac, encendido=estado_bool)` (`app/mqtt_listener.py:63`)
4. **Known Bug:** This sets `is_encendido` (relay state) instead of `is_online` (reachability) — see Anti-Patterns below

### Device State Query (REST → DB)

1. Mobile app sends `GET /api/dispositivos/{mac}/estado` (`app/main.py:80-86`)
2. Endpoint queries `Artefacto.is_online` via `obtener_estado_dispositivo` (`app/crud.py:57-67`)
3. Returns `DispositivoEstadoResponse` with `mac_dispositivo` and `is_online`

**State Management:**
- No in-memory state — all device state lives in MariaDB
- `Artefacto.is_online` tracks MQTT liveness (set by birth/LWT)
- `Artefacto.is_encendido` tracks physical relay state (set by user commands)
- `Artefacto.estado_deseado` / `Artefacto.estado_reportado` device shadow fields exist in schema but are not yet written by any code path

## Key Abstractions

**Artefacto (Device Shadow):**
- Purpose: Central device model combining identity, configuration, liveness, and lease state
- Pattern: Aggregate root with cascading relationships to alerts, credentials, OTA deployments, events, and ACL permissions
- Fields: `mac`, `nombre_personalizado`, `nivel_prioridad`, `limite_consumo_w`, `is_online`, `is_encendido`, `estado_deseado`, `estado_reportado`, `override_activo`, `vencimiento_lease`
- Example: `app/models.py:18-45`

**Telemetria (Time-Series Partition):**
- Purpose: High-frequency sensor data stored with monthly range partitioning
- Pattern: Composite primary key `(id, timestamp)` required for MariaDB `PARTITION BY RANGE`; no `FOREIGN KEY` on `id_artefacto` (MariaDB partitioning limitation)
- Example: `app/models.py:107-119`, `schema_iot.sql:89-110`

**PermisoAppArtefacto (ACL Junction):**
- Purpose: Many-to-many relationship between API keys and devices with access level
- Pattern: Composite primary key `(id_api_key, id_artefacto)` with cascading FK deletes
- Example: `app/models.py:47-56`

**AsyncSession Dependency Injection:**
- Purpose: Provide per-request async DB sessions to FastAPI endpoints
- Pattern: `get_db()` async generator yields session, auto-closes on request completion
- Example: `app/database.py:48-57`

**MQTT-to-Async Bridge:**
- Purpose: Allow synchronous paho-mqtt callback to schedule work on the FastAPI async event loop
- Pattern: `asyncio.run_coroutine_threadsafe()` schedules `procesar_payload()` from the MQTT thread onto the main event loop
- Example: `app/mqtt_listener.py:29-30`

## Entry Points

**FastAPI Application (uvicorn):**
- Location: `app/main.py:29`
- Triggers: `uvicorn app.main:app --reload`
- Responsibilities: Creates FastAPI app, registers lifespan hook, defines all REST endpoints

**MQTT Listener Initialization:**
- Location: `app/mqtt_listener.py:71-90` (`iniciar_oyente_mqtt()`)
- Triggers: FastAPI lifespan startup (`app/main.py:24`)
- Responsibilities: Captures running event loop, creates MQTT client, subscribes to topics, starts background thread

**ESP32 Simulator:**
- Location: `app/mock_esp32.py`
- Triggers: `python -m app.mock_esp32`
- Responsibilities: Publishes fake telemetry, processes commands from server, simulates birth/LWT messages

**Database Connectivity Test:**
- Location: `test_db.py`
- Triggers: `python test_db.py`
- Responsibilities: Validates MariaDB connection using async SQLAlchemy engine

## Architectural Constraints

- **Threading:** Two threads — main thread runs FastAPI async event loop; background thread runs paho-mqtt network loop. Communication via `asyncio.run_coroutine_threadsafe()`. The GIL serializes Python bytecodes, but I/O operations (network, DB) yield correctly.
- **Global state:** `_main_loop` module-level variable in `app/mqtt_listener.py:9` stores the asyncio event loop reference for cross-thread scheduling. `estado_rele_encendido` global in `app/mock_esp32.py:10` tracks simulated relay state.
- **Circular imports:** None detected. Imports flow downward: `main.py` → `crud.py` → `models.py` → `database.py`. `mqtt_listener.py` imports from `database.py`, `crud.py`, and `schemas.py`.
- **No authentication middleware:** REST endpoints have no auth checks. `AppApiKey` and `PermisoAppArtefacto` models exist in the schema but no enforcement code uses them.
- **No `__init__.py`:** The `app/` package has no `__init__.py`. All imports use `app.` prefix and rely on uvicorn resolving the package from the project root working directory.
- **Partitioning constraint:** `telemetria.id_artefacto` cannot have a `FOREIGN KEY` due to MariaDB partitioning limitations. Referential integrity must be enforced at the application level.
- **Hardcoded MQTT credentials:** `app/main.py:56,74` hardcodes MQTT auth (`esp-gateway` / hardcoded password). `app/mqtt_listener.py:13-14` uses env vars with hardcoded fallback. These must be kept in sync manually.
- **Single-file modules:** Each responsibility lives in a flat `.py` file inside `app/` — no sub-package organization exists yet.

## Anti-Patterns

### Conexion handler updates wrong field

**What happens:** `app/mqtt_listener.py:63` calls `actualizar_estado_dispositivo(db, mac, encendido=estado_bool)` when processing a `conexion` MQTT message. This function sets `Artefacto.is_encendido` (physical relay state), but the intent from the MQTT birth/LWT message is to set `is_online` (network reachability).

**Why it's wrong:** The `GET /api/dispositivos/{mac}/estado` endpoint returns `is_online` — which never gets updated by the MQTT path because the wrong field is written. Devices appear permanently offline via the API even though they are connected.

**Do this instead:** Create a dedicated `actualizar_conexion_dispositivo(db, mac, is_online: bool)` function in `app/crud.py` that sets `Artefacto.is_online` and optionally updates `last_seen_at`. Use it in the `conexion` handler in `app/mqtt_listener.py`.

### Hardcoded MQTT credentials in REST endpoints

**What happens:** `app/main.py:56` and `app/main.py:74` hardcode `{'username': "esp-gateway", 'password': "wUbcJJiZcLqV3dDo2r9e"}` for `paho.mqtt.publish.single()` calls, while `app/mqtt_listener.py:13-14` reads from env vars with a hardcoded fallback.

**Why it's wrong:** Credentials are duplicated and inconsistent — env vars vs hardcoded. Publishing from REST will use the wrong credentials if env vars change, and the password is committed in source code.

**Do this instead:** Extract MQTT credentials into a shared config module (e.g., `app/config.py`) that reads exclusively from env vars. Import and use the same config object in both `main.py` and `mqtt_listener.py`.

### Shared CRUD function for distinct domain actions

**What happens:** `actualizar_estado_dispositivo(db, mac, encendido=...)` in `app/crud.py:42-55` is used for both relay-on/off commands (from REST endpoint) and connection status updates (from MQTT). These are semantically different operations.

**Why it's wrong:** The function name and parameter name (`encendido`) suggest relay control, but it's called for connection events too — causing the bug described above. A single function serving two purposes violates single responsibility.

**Do this instead:** Split into two functions: `actualizar_relay_dispositivo(db, mac, encendido: bool)` and `actualizar_conexion_dispositivo(db, mac, is_online: bool)`. Use each at the appropriate call site.

## Error Handling

**Strategy:** Minimal — rely on FastAPI's exception handling and SQLAlchemy's error propagation.

**Patterns:**
- REST endpoints raise `HTTPException(status_code=404)` when devices are not found (`app/main.py:45,52,67,84`)
- MQTT listener catches all exceptions with a bare `except Exception` and logs to stdout (`app/mqtt_listener.py:68-69`) — errors are silently swallowed after logging
- Database operations use `scalar_one_or_none()` which returns `None` on no match — callers check for `None` and return appropriate 404s
- No structured error logging framework — all logging uses `print()` to stdout with emoji prefixes
- `async with AsyncSessionLocal() as session` context manager in MQTT listener ensures session cleanup (`app/mqtt_listener.py:45`)

## Cross-Cutting Concerns

**Logging:** Ad-hoc `print()` statements with emoji prefixes across `app/mqtt_listener.py` and `app/mock_esp32.py`. `app/main.py` uses `logging.getLogger("uvicorn.error")` but only declares it — never writes to it. No structured logging, no log levels, no log aggregation.

**Validation:** Pydantic schemas enforce field types and constraints (`ge=0` on numerics, `min_length=17`/`max_length=17` on MAC). No server-side validation of MAC address format beyond length. No API key auth validation on any endpoint.

**Authentication:** None enforced. `AppApiKey` and `PermisoAppArtefacto` models exist in the database schema but no middleware or dependency checks them. All REST endpoints are publicly accessible.

**Configuration:** Environment variables loaded from `.env` file via `python-dotenv` (`app/database.py:7`). Database env vars are required (`DB_USER`, `DB_PASSWORD`, `DB_NAME`). MQTT env vars are optional (`MQTT_USER`, `MQTT_PASS`) with hardcoded fallbacks.

---

*Architecture analysis: 2026-05-12*