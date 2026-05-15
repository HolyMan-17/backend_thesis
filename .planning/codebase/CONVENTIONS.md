# Coding Conventions

**Analysis Date:** 2026-05-12

## Naming Patterns

**Files:**
- Snake_case module names: `mqtt_listener.py`, `mock_esp32.py`
- One concern per file: `models.py` (ORM), `schemas.py` (Pydantic), `crud.py` (DB ops), `database.py` (engine)
- SQL schema file at project root: `schema_iot.sql`

**Functions:**
- Spanish snake_case for all domain functions: `crear_telemetria`, `obtener_telemetria_por_mac`, `actualizar_estado_dispositivo`, `obtener_estado_dispositivo`
- English snake_case for infrastructure helpers: `get_db`, `health_check`
- Async functions use `async def` prefix consistently

**Variables:**
- Spanish domain terms: `mac_dispositivo`, `telemetria_in`, `nueva_metrica`, `dispositivo`, `estado_bool`
- English for infrastructure: `db`, `stmt`, `result`, `client`, `payload`
- Module-level constants are UPPER_SNAKE_CASE: `MQTT_USER`, `MQTT_PASS`, `MAC_ESP32`, `BROKER`, `DATABASE_URL`, `AsyncSessionLocal`

**Types / Classes:**
- PascalCase for both SQLAlchemy models and Pydantic schemas: `Artefacto`, `Telemetria`, `TelemetriaCreate`, `DispositivoEstado`
- Model class names match table names (singular, Spanish): `AppApiKey`, `Artefacto`, `PermisoAppArtefacto`, `AlertaSistema`, `CredencialMtls`, `DespliegueOta`, `EventoUsuario`, `Telemetria`

**Database Columns:**
- Spanish snake_case throughout: `mac`, `nombre_personalizado`, `nivel_prioridad`, `limite_consumo_w`, `estado_deseado`, `is_online`, `is_encendido`, `last_seen_at`, `override_activo`, `vencimiento_lease`, `tiempo_operacion_s`, `estado_sin_cambios`
- English terms used only for well-known fields: `is_online`, `id`, `timestamp`

## Spanish Domain Vocabulary

All domain identifiers use Spanish. Key translations to internalize:

| Spanish Term | English Meaning | Used In |
|---|---|---|
| `artefacto` | device/appliance | `Artefacto` model, `artefactos` table |
| `telemetria` | telemetry | `Telemetria` model, endpoint paths |
| `encendido` | turned on (relay state) | `is_encendido` column, `DispositivoEstado.encendido` |
| `conexion` | connection (network) | MQTT topic suffix `conexion` |
| `dispositivo` | device | Schema fields `mac_dispositivo` |
| `crear_` | create | `crear_telemetria` |
| `obtener_` | get/retrieve | `obtener_telemetria_por_mac` |
| `actualizar_` | update | `actualizar_estado_dispositivo` |
| `permiso` | permission | `PermisoAppArtefacto` |
| `alerta` | alert | `AlertaSistema` |
| `credencial` | credential | `CredencialMtls` |
| `despliegue` | deployment | `DespliegueOta` |
| `evento` | event | `EventoUsuario` |

## Critical Semantic Distinction

**`is_encendido` vs `is_online` — these are NOT interchangeable:**
- `is_encendido` = physical relay on/off state (set by user commands via `/api/comando/estado`)
- `is_online` = network reachability (set by MQTT birth/LWT messages)
- The `GET /api/dispositivos/{mac}/estado` endpoint returns `is_online`, NOT `is_encendido`
- `actualizar_estado_dispositivo()` sets `is_encendido` — do NOT use it for `is_online`

## Code Style

**Formatting:**
- No formatter config detected (no `.prettierrc`, `.editorconfig`, or `black` config)
- Indentation appears consistent at 4 spaces
- Blank lines between logical groupings in models (relationships separated from columns)
- Single-space around `=` in assignments

**Linting:**
- No linter config detected (no `.flake8`, `.pylintrc`, `ruff.toml`, or `mypy.ini`)
- No type checking enforcement

**Max line length:** Not enforced — some lines exceed 100 chars (e.g., `app/main.py:22`, `app/mqtt_listener.py:14`)

## Import Organization

**Order (observed in `app/main.py`, `app/mqtt_listener.py`):**

1. Standard library: `typing`, `json`, `logging`, `os`, `contextlib`, `asyncio`
2. Third-party: `paho.mqtt`, `fastapi`, `sqlalchemy`
3. Local application: `from app.database import ...`, `from app.models import ...`, `from app.schemas import ...`, `from app.crud import ...`

**Path Aliases:**
- All local imports use `app.` prefix: `from app.database import get_db`
- No `__init__.py` exists in `app/` — the `app.` prefix works because uvicorn resolves the package from the project root
- **New files must follow the same pattern:** use `from app.module import Class` for cross-module imports

**Import style:**
- Grouped `from` imports with parentheses for multiple names:
  ```python
  from app.schemas import (
      TelemetriaCreate, TelemetriaResponse, 
      DispositivoEstado, DispositivoLimites, DispositivoEstadoResponse
  )
  ```

## Error Handling

**Patterns:**

- **Endpoint errors:** Use `HTTPException` from FastAPI with explicit status codes:
  ```python
  # From app/main.py:44-45
  if nueva_telemetria is None:
      raise HTTPException(status_code=404, detail="Dispositivo no registrado")
  ```

- **Not-found pattern in CRUD:** Return `None` or `False`, let the caller raise `HTTPException`:
  ```python
  # From app/crud.py:47-48
  if not dispositivo:
      return False
  ```

- **MQTT handler errors:** Broad `except Exception` with emoji-prefixed `print()`:
  ```python
  # From app/mqtt_listener.py:68-69
  except Exception as e:
      print(f"❌ Error en Worker {os.getpid()}: {e}", flush=True)
  ```

- **No custom exception classes** — all errors use `HTTPException` or bare `print()`

- **`ValueError` for startup validation** in `database.py`:
  ```python
  # From app/database.py:17-18
  if not all([DB_USER, DB_PASSWORD, DB_NAME]):
      raise ValueError("Missing critical database environment variables in .env file.")
  ```

**Inconsistency:** `status` module is imported (`from fastapi import ... status`) but `comando_estado` and `comando_limites` use hardcoded `status_code=404` instead of `status.HTTP_404_NOT_FOUND`, while `registrar_telemetria` correctly uses `status.HTTP_201_CREATED`.

## Logging

**Framework:** Mixed — `logging` module in `main.py`, `print()` in MQTT code

**Patterns:**
- `app/main.py` creates a `logging.getLogger("uvicorn.error")` logger but only defines it — no explicit log calls are present in the file
- `app/mqtt_listener.py` uses `print()` with emoji prefixes and `flush=True` throughout:
  ```python
  print(f"🔌 Worker {os.getpid()} de FastAPI conectado a Mosquitto", flush=True)
  print(f"❌ Error en Worker {os.getpid()}: {e}", flush=True)
  ```
- `app/mock_esp32.py` uses the same `print()` + emoji pattern
- **Convention for new code:** Use emoji-prefixed `print()` for MQTT-related output (to match existing style), but prefer `logging` for application-level code

## Comments

**When to Comment:**
- Critical architectural constraints: MariaDB partitioning note in `app/models.py:111`
- Semantic clarifications: relay vs network state comments in `app/crud.py:50-51, 65-66`
- API contract constraints: "Return empty object" / "Contract constraint" in `app/main.py:59, 78`
- Security notes: "DevSecOps" comment about env vars in `app/mqtt_listener.py:11-12`
- Spanish-language inline comments: Used throughout `mqtt_listener.py` and `mock_esp32.py`

**JSDoc/TSDoc:**
- Only one docstring found in the entire codebase: `app/database.py:49-52` (`get_db()`)
- **Convention:** Add docstrings for dependency-injection functions and public API surface. It's acceptable to omit docstrings for simple CRUD functions.

## Function Design

**Size:** Most functions are 5–15 lines. Keep functions small and focused.

**Parameters:**
- `db: AsyncSession` is always the first parameter in CRUD functions, injected via FastAPI `Depends(get_db)`
- Pydantic schema objects are used for request bodies: `telemetria_in: TelemetriaCreate`
- Path parameters use FastAPI path syntax: `mac_dispositivo: str`
- Query parameters have defaults: `limite: int = 50`

**Return Values:**
- CRUD functions return ORM model instances or `None`/`False` for not-found:
  - `crear_telemetria()` → `Telemetria | None`
  - `obtener_telemetria_por_mac()` → `list[Telemetria] | None`
  - `actualizar_estado_dispositivo()` → `bool`
  - `obtener_estado_dispositivo()` → `bool | None`
- Endpoints return Pydantic response models or empty dicts `{}` for command endpoints

## Module Design

**Exports:**
- No barrel files — each module exports classes/functions directly
- Only public names are imported: models, schema classes, CRUD functions
- Internal module state is module-level globals (`_main_loop` in `mqtt_listener.py`)

**Barrel Files:** None — each module is imported directly by full path

**Flat module structure:** All application modules live directly in `app/` with no sub-packages. New modules should also be flat files in `app/`.

## Pydantic Schemas

**Pattern:**
- Base → Create → Response inheritance chain:
  ```python
  # From app/schemas.py
  class TelemetriaBase(BaseModel):   # shared fields
  class TelemetriaCreate(TelemetriaBase):  # input fields
  class TelemetriaResponse(TelemetriaBase):  # output fields
  ```
- `Config` class with `from_attributes = True` on response schemas
- `Field()` validators used for constraints: `ge=0`, `min_length=17`, `max_length=17`
- Partial updates use `Optional[...] = None` fields:
  ```python
  # From app/schemas.py:31-36
  class DispositivoLimites(BaseModel):
      mac_dispositivo: str
      limite_voltaje: Optional[float] = None
      limite_corriente: Optional[float] = None
      limite_potencia: Optional[float] = None
  ```
- Serialization uses `model_dump(exclude_unset=True)` for partial updates

## SQLAlchemy Models

**Pattern:**
- Declarative base from `app.database.Base`
- Column-based definitions (not `mapped_column`)
- Spanish table names via `__tablename__`: `artefactos`, `telemetria`, `permisos_app_artefacto`
- Relationships use `back_populates` and `cascade="all, delete-orphan"`
- `func.now()` for server-side timestamp defaults
- `server_default="0"` for database-level defaults, Python `default=False` for ORM-level defaults
- Composite primary keys for junction tables (`PermisoAppArtefacto`)
- Foreign keys include `ondelete="CASCADE"` at the DB level

**Special note on `Telemetria`:**
- Composite PK `(id, timestamp)` for MariaDB range partitioning
- `id_artefacto` has NO foreign key constraint (MariaDB partitioning limitation) — noted in comment at `app/models.py:111`

## API Endpoint Conventions

**Path style:** `/api/{resource}/{identifier}/{sub-resource}` — all Spanish:
  - `GET /api/telemetria/{mac_dispositivo}`
  - `POST /api/telemetria`
  - `POST /api/comando/estado`
  - `POST /api/comando/limites`
  - `GET /api/dispositivos/{mac_dispositivo}/estado`

**Health check:** `/health` at root level (English), returns `{}`

**Command endpoints** (`/api/comando/*`) return `{}` (empty object) per contract

**Response model annotation:** `response_model=List[TelemetriaResponse]` or `response_model=DispositivoEstadoResponse`

**Status codes:** Import `status` from FastAPI — use symbolic names (`status.HTTP_201_CREATED`, `status.HTTP_404_NOT_FOUND`) rather than raw integers. Current code is inconsistent on this; prefer symbolic names in new code.

## Environment Configuration

**Pattern:**
- `python-dotenv` loads `.env` from project root: `load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))` in `app/database.py:7`
- `os.getenv()` with fallback defaults in `app/mqtt_listener.py:13-14`
- Startup validation raises `ValueError` for missing critical env vars in `app/database.py:17-18`

**Critical env vars:**
- `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME` (required)
- `MQTT_USER`, `MQTT_PASS` (optional, has hardcoded fallbacks — a security concern)

## Async Patterns

**All I/O is async:**
- FastAPI endpoints: `async def`
- SQLAlchemy queries: `await db.execute(stmt)`
- Session lifecycle: `async with AsyncSessionLocal() as session`
- MQTT bridge: `asyncio.run_coroutine_threadsafe()` to schedule coroutines from the MQTT background thread onto the main event loop

**Global event loop reference:** `mqtt_listener.py` stores `_main_loop` as a module-level global, populated during `iniciar_oyente_mqtt()` called from the FastAPI lifespan handler.

---

*Convention analysis: 2026-05-12*