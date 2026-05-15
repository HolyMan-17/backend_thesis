# Codebase Structure

**Analysis Date:** 2026-05-12

## Directory Layout

```
iot_backend/
├── .env                        # Environment variables (DB + MQTT creds) — DO NOT READ
├── .planning/                  # GSD planning artifacts
│   └── codebase/               # Codebase analysis documents
├── AGENTS.md                   # Agent instructions and project notes
├── app/                        # Application source package (no __init__.py)
│   ├── crud.py                 # Async DB read/write operations
│   ├── database.py             # SQLAlchemy async engine + session factory
│   ├── main.py                 # FastAPI app, lifespan, all REST endpoints
│   ├── mock_esp32.py           # Standalone ESP32 device simulator
│   ├── models.py               # SQLAlchemy ORM models (Spanish names)
│   ├── mqtt_listener.py        # MQTT subscriber (background thread)
│   └── schemas.py              # Pydantic request/response schemas
├── schema_iot.sql              # MariaDB DDL — run once to bootstrap DB
├── skills/                     # Agent skills
│   └── grill-me/               # Interview skill
├── test_db.py                  # DB connectivity test script
└── venv/                       # Python virtual environment (not tracked)
```

## Directory Purposes

**`app/`:**
- Purpose: All application source code — the entire backend is in this flat package
- Contains: 7 Python modules, each handling a single responsibility
- Key files: `app/main.py` (HTTP), `app/mqtt_listener.py` (MQTT), `app/crud.py` (DB ops)
- No `__init__.py` — imports use `app.` prefix, resolved from project root by uvicorn

**`app/__pycache__/`:**
- Purpose: Python bytecode cache (auto-generated, not committed)
- Contains: Compiled `.pyc` files
- Generated: Yes
- Committed: No

**Project root (`iot_backend/`):**
- Purpose: Configuration, DDL, test scripts, environment
- Contains: `.env`, `schema_iot.sql`, `test_db.py`, `AGENTS.md`
- Key distinction: The project root is the working directory for `uvicorn app.main:app`

**`.planning/`:**
- Purpose: GSD tool planning artifacts (codebase analysis, phase plans)
- Contains: `codebase/` subdirectory with analysis docs
- Generated: Yes (by GSD tools)
- Committed: Yes

**`skills/`:**
- Purpose: Agent skill definitions
- Contains: `grill-me/` skill subdirectory
- Not part of application runtime

**`venv/`:**
- Purpose: Python virtual environment
- Contains: All installed packages (fastapi, uvicorn, sqlalchemy, etc.)
- Generated: Yes
- Committed: No

## Key File Locations

**Entry Points:**
- `app/main.py`: FastAPI application factory + all REST endpoints + lifespan manager
- `app/mock_esp32.py`: Standalone script — run with `python -m app.mock_esp32`
- `test_db.py`: Standalone connectivity test — run with `python test_db.py`

**Configuration:**
- `app/database.py`: DB engine creation, session factory, env var loading (`.env` from project root)
- `app/mqtt_listener.py:13-14`: MQTT credentials (env vars with hardcoded fallback)
- `app/main.py:56,74`: MQTT publish credentials (hardcoded — should use env vars)
- `.env`: DB and MQTT credentials (project root, gitignored)
- `schema_iot.sql`: MariaDB DDL — run once to create all tables and partitions

**Core Logic:**
- `app/crud.py`: All database read/write operations (4 functions)
- `app/mqtt_listener.py`: MQTT message processing, topic parsing, async dispatch
- `app/models.py`: 8 ORM models with relationships and column definitions

**Data Models:**
- `app/models.py`: SQLAlchemy ORM — Artefacto, Telemetria, AppApiKey, PermisoAppArtefacto, AlertaSistema, CredencialMtls, DespliegueOta, EventoUsuario
- `app/schemas.py`: Pydantic — TelemetriaBase, TelemetriaCreate, TelemetriaResponse, DispositivoEstado, DispositivoLimites, DispositivoEstadoResponse

**Infrastructure:**
- `app/database.py`: Async engine, session maker, `Base` class, `get_db()` dependency
- `app/mqtt_listener.py`: MQTT client lifecycle, topic subscriptions, event loop bridge

**Testing:**
- `test_db.py`: Manual DB connectivity verification only — no test framework configured
- No `tests/` directory exists
- No `pytest.ini`, `conftest.py`, or test configuration files

## Naming Conventions

**Files:**
- Python modules use `snake_case.py`: `main.py`, `crud.py`, `database.py`, `models.py`, `schemas.py`, `mqtt_listener.py`, `mock_esp32.py`
- SQL file uses `snake_case.sql`: `schema_iot.sql`
- Test file: `test_db.py` (prefixed with `test_`)

**Database Tables (Spanish):**
- Plural nouns in Spanish: `artefactos`, `telemetria`, `app_api_keys`, `permisos_app_artefacto`, `alertas_sistema`, `credenciales_mtls`, `despliegues_ota`, `eventos_usuario`
- Notable: `telemetria` and `app_api_keys` are singular/mixed — not consistently pluralized

**Database Columns (Spanish):**
- `snake_case` in Spanish: `nombre_personalizado`, `nivel_prioridad`, `limite_consumo_w`, `estado_deseado`, `estado_reportado`, `vencimiento_lease`
- Exception: `is_online`, `is_encendido` — English `is_` prefix (boolean convention)
- Timestamps: `fecha_creacion`, `fecha_asignacion`, `fecha_emision`, `fecha_despliegue`, `last_seen_at`

**Python Variables and Functions (Spanish):**
- Functions: `snake_case` in Spanish: `crear_telemetria`, `obtener_telemetria_por_mac`, `actualizar_estado_dispositivo`, `iniciar_oyente_mqtt`, `procesar_payload`
- Variables: `snake_case` in Spanish: `mac_desde_topic`, `tipo_mensaje`, `estado_rele_encendido`, `credenciales_mqtt`
- Exceptions: `_main_loop` (English), `nueva_metrica` (Spanish)

**REST Endpoints (Spanish):**
- URL paths use Spanish resource names: `/api/telemetria`, `/api/comando/estado`, `/api/comando/limites`, `/api/dispositivos/{mac}/estado`
- Exception: `/health` (English)

**MQTT Topics (Spanish/English hybrid):**
- `smartups/dispositivos/{mac}/telemetria` — Spanish
- `smartups/dispositivos/{mac}/conexion` — Spanish
- `smartups/dispositivos/{mac}/comando/estado` — Spanish
- `smartups/dispositivos/{mac}/comando/limites` — Spanish

**Pydantic Schemas:**
- PascalCase class names in Spanish: `TelemetriaCreate`, `TelemetriaResponse`, `DispositivoEstado`, `DispositivoEstadoResponse`, `DispositivoLimites`
- Exception: `TelemetriaBase`, `TelemetriaCreate` — English suffix (`Base`, `Create`, `Response`)

## Where to Add New Code

**New REST Endpoint:**
- Add route handler function in `app/main.py`
- Add Pydantic request/response schemas in `app/schemas.py`
- Add async DB operation function in `app/crud.py`
- Add ORM model if needed in `app/models.py`
- Add DDL in `schema_iot.sql` if new table needed

**New MQTT Topic Subscription:**
- Add `client.subscribe(...)` call in `on_connect()` in `app/mqtt_listener.py`
- Add message type handler in `procesar_payload()` in `app/mqtt_listener.py`
- Add corresponding CRUD function in `app/crud.py` if DB write needed

**New ORM Model:**
- Add class to `app/models.py` inheriting from `Base`
- Use Spanish naming for `__tablename__` and columns
- Add corresponding `CREATE TABLE` DDL in `schema_iot.sql`
- If the table needs FK to `artefactos`, add a `relationship()` on the `Artefacto` model
- If the table is `telemetria`-like (time-series), consider range partitioning and omit FK on `id_artefacto`

**New Pydantic Schema:**
- Add class to `app/schemas.py`
- Follow existing pattern: `XxxBase` → `XxxCreate` / `XxxResponse`
- Use `from_attributes = True` Config subclass for response models that map to ORM
- Use `Field(...)` for validation constraints

**New CRUD Function:**
- Add async function to `app/crud.py`
- Always accept `db: AsyncSession` as first parameter
- Use `await db.commit()` + `await db.refresh()` pattern for writes
- Return `None` or `False` for "not found" conditions; let callers raise HTTP 404

**New Configuration/Environment Variable:**
- Add to `.env` in project root
- Read in `app/database.py` (for DB config) or create `app/config.py` (for other config)
- Never hardcode credentials — always prefer env vars

**New Database Table (partitioned):**
- Define DDL in `schema_iot.sql` with `PARTITION BY RANGE`
- Use composite primary key `(id, timestamp)` for partitioned tables
- Do NOT add `FOREIGN KEY` on `id_artefacto` for partitioned tables (MariaDB limitation)
- Add monthly partitions through `p_max` catch-all

**Utilities or Shared Helpers:**
- Currently no utility module exists
- Create `app/utils.py` or `app/helpers.py` for shared logic
- For MQTT config, create `app/config.py` to centralize env var reading

## Special Directories

**`app/__pycache__/`:**
- Purpose: Python bytecode cache
- Generated: Yes (automatically by Python interpreter)
- Committed: No (should be in `.gitignore`)

**`venv/`:**
- Purpose: Python virtual environment with all dependencies
- Generated: Yes (created by `python -m venv venv`)
- Committed: No
- Key packages: fastapi, uvicorn, sqlalchemy, aiomysql, pymysql, paho-mqtt, python-dotenv, pydantic

**`.planning/`:**
- Purpose: GSD tool planning and analysis artifacts
- Generated: Yes (by `/gsd-map-codebase` and related commands)
- Committed: Yes (tracks project understanding over time)

---

*Structure analysis: 2026-05-12*