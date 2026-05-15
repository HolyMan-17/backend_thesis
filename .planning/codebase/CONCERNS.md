# Codebase Concerns

**Analysis Date:** 2026-05-12

## Tech Debt

### No dependency management file
- Issue: No `requirements.txt`, `pyproject.toml`, or `Pipfile` exists. Dependencies are only installed in the venv with no lockfile or pinned versions.
- Files: Project root (`/home/manu0ak/iot_backend/`)
- Impact: Cannot reliably reproduce the build environment. New developer onboarding or CI/CD pipeline setup requires manual inspection of AGENTS.md to know what to install. Version conflicts are inevitable as there are no version pins.
- Fix approach: Create `requirements.txt` with pinned versions by running `pip freeze > requirements.txt` inside the venv. Consider migrating to `pyproject.toml` for modern Python packaging.

### Missing `app/__init__.py`
- Issue: The `app/` directory has no `__init__.py`. The package relies on uvicorn resolving the `app.` prefix from the project root working directory.
- Files: `app/__init__.py` (missing)
- Impact: Import paths like `from app.database import ...` work only when the process is launched from `/home/manu0ak/iot_backend/` with the correct PYTHONPATH. Any IDE, test runner, or alternate entry point may fail to resolve imports.
- Fix approach: Add an empty `app/__init__.py`. This is a zero-risk change that makes the package self-consistent.

### No `rollback` on database errors
- Issue: The CRUD functions in `app/crud.py` call `db.commit()` but never call `db.rollback()` on failure. The `except` blocks in `app/mqtt_listener.py:68-69` catch exceptions after `procesar_payload` calls CRUD functions, but don't roll back the session.
- Files: `app/crud.py:26`, `app/mqtt_listener.py:68-69`
- Impact: If a commit fails mid-transaction, the SQLAlchemy session enters an invalid state. Subsequent operations on the same session will raise `PendingRollbackError`. The `async with AsyncSessionLocal() as db:` context manager in `mqtt_listener.py:45` does close the session, but dirty state may leak between operations within the same context.
- Fix approach: Add explicit `try/except` with `await db.rollback()` in each CRUD function, or use the `async with db.begin():` context manager which auto-commits/rollbacks.

### Broad exception swallowing in MQTT listener
- Issue: `app/mqtt_listener.py:68-69` catches all `Exception` and only prints the message. No distinction between transient (network timeout) and permanent (schema mismatch) errors.
- Files: `app/mqtt_listener.py:68-69`
- Impact: Malformed payloads or schema changes silently drop data. No metrics, no retry logic, no dead-letter queue.
- Fix approach: Catch specific exceptions (`json.JSONDecodeError`, `sqlalchemy.exc.DBAPIError`, `ValidationError`) and log with appropriate severity. Consider adding a retry mechanism for transient errors.

### Hardcoded MQTT host
- Issue: The MQTT broker address `127.0.0.1` is hardcoded in three places: `app/main.py:57`, `app/main.py:75`, `app/mqtt_listener.py:86`, `app/mock_esp32.py:46`.
- Files: `app/main.py:57`, `app/main.py:75`, `app/mqtt_listener.py:86`, `app/mock_esp32.py:46`
- Impact: Cannot deploy to a different environment (staging, production) without code changes. The broker address should be an environment variable like `MQTT_HOST` / `MQTT_PORT`.
- Fix approach: Add `MQTT_HOST` and `MQTT_PORT` env vars (with `127.0.0.1` / `1883` defaults) in `app/database.py` or a new `app/config.py`, and reference them everywhere.

## Known Bugs

### `conexion` handler sets `is_encendido` instead of `is_online` (CRITICAL)
- Symptoms: When an ESP32 sends a Birth/LWT message on the `conexion` topic, the handler at `app/mqtt_listener.py:63` calls `actualizar_estado_dispositivo(db, mac_desde_topic, encendido=estado_bool)`. This function (`app/crud.py:52`) sets `dispositivo.is_encendido = encendido`, which is the physical relay on/off state. But the MQTT `conexion` topic carries `is_online` (network reachability), not relay state.
- Files: `app/mqtt_listener.py:58-63`, `app/crud.py:42-55`
- Trigger: Any ESP32 birth/LWT message. Every time a device connects or disconnects from the network, its relay state (`is_encendido`) gets incorrectly overwritten with its online status.
- Workaround: None. This causes data corruption — the `is_encendido` field becomes unreliable.
- Fix approach: Create a dedicated `actualizar_conexion_dispositivo(db, mac, is_online: bool)` function that sets `dispositivo.is_online` and updates `dispositivo.last_seen_at`. Replace the call at `app/mqtt_listener.py:63` with the new function. Optionally also update `last_seen_at` to the current timestamp.

### Hardcoded MQTT credentials in `main.py`
- Symptoms: `app/main.py:56` and `app/main.py:74` embed MQTT username and password as string literals (`esp-gateway` / `wUbcJJiZcLqV3dDo2r9e`). Meanwhile `app/mqtt_listener.py:13-14` reads from environment variables with hardcoded fallback defaults.
- Files: `app/main.py:56`, `app/main.py:74`, `app/mqtt_listener.py:13-14`
- Trigger: Always — every REST endpoint that publishes an MQTT command leaks credentials into source code.
- Workaround: None. Credentials are in the git-tracked codebase.
- Fix approach: Refactor both `main.py` and `mqtt_listener.py` to use a shared config module that reads `MQTT_USER` / `MQTT_PASS` from environment variables only. Remove hardcoded fallback values from `mqtt_listener.py:14` as well.

### `mock_esp32.py` also hardcodes MQTT credentials
- Symptoms: `app/mock_esp32.py:42` hardcodes the same credentials in a `username_pw_set()` call.
- Files: `app/mock_esp32.py:42`
- Trigger: Always when running the simulator.
- Workaround: Manually edit the file before running.
- Fix approach: Use environment variables or a shared config module, consistent with the fix for `main.py`.

## Security Considerations

### No authentication or authorization on REST endpoints
- Risk: Every endpoint (`GET /api/telemetria/{mac}`, `POST /api/telemetria`, `POST /api/comando/estado`, `POST /api/comando/limites`, `GET /api/dispositivos/{mac}/estado`) is publicly accessible. Any network-reachable client can read device telemetry, toggle relays, change limits, and enumerate device status.
- Files: `app/main.py:36-86`
- Current mitigation: None. There is no auth middleware, no API key check, no CORS restriction.
- Recommendations: Implement API key authentication using the existing `app_api_keys` table and `AppApiKey` model (`app/models.py:6-16`). Add a FastAPI dependency that validates the `X-API-Key` header against `app_api_keys.api_key_hash`. Also add CORS middleware with an explicit allowlist.

### Hardcoded MQTT credentials in source code
- Risk: Credentials (`esp-gateway` / `wUbcJJiZcLqV3dDo2r9e`) are committed in plain text in `app/main.py:56`, `app/main.py:74`, `app/mqtt_listener.py:14`, and `app/mock_esp32.py:42`.
- Files: `app/main.py:56`, `app/main.py:74`, `app/mqtt_listener.py:14`, `app/mock_esp32.py:42`
- Current mitigation: `mqtt_listener.py` reads from env vars as primary source, but has a hardcoded fallback that defeats the purpose.
- Recommendations: Remove all hardcoded credential strings. Use a shared config module with env-only reads and fail-fast on missing values (no defaults for passwords). Rotate the exposed credentials immediately.

### No input validation on MAC address format
- Risk: The MAC address in URL paths (`/api/telemetria/{mac_dispositivo}`, `/api/dispositivos/{mac_dispositivo}/estado`) is a plain `str` with no regex validation. An attacker could inject unexpected strings.
- Files: `app/main.py:37`, `app/main.py:80`
- Current mitigation: `TelemetriaCreate.mac_dispositivo` in `app/schemas.py:14` validates `min_length=17, max_length=17`, but this only applies to POST bodies, not URL path parameters.
- Recommendations: Add a Pydantic `Field` with `pattern=r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$'` for all MAC path parameters. Consider a shared `MacAddress` type.

### No CORS configuration
- Risk: The FastAPI app has no `CORSMiddleware` at all, which means browser-based requests from any origin are allowed by default (browsers block cross-origin lacking appropriate headers, but the server doesn't restrict anything).
- Files: `app/main.py`
- Current mitigation: None.
- Recommendations: Add `CORSMiddleware` with explicit `allow_origins` list. Never use `allow_origins=["*"]` in production.

### `.env` file exists at project root — must not be committed
- Risk: The `.env` file at `/home/manu0ak/iot_backend/.env` contains `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME` (and possibly `MQTT_USER`, `MQTT_PASS`). If committed, this leaks database and broker credentials.
- Files: `.env` (exists, contents not read per policy)
- Current mitigation: Unknown — no `.gitignore` file was found during analysis.
- Recommendations: Ensure `.env` is in `.gitignore`. Never commit it. Consider using a secrets manager for production.

## Performance Bottlenecks

### Telemetry query lacks composite index for MAC + timestamp
- Problem: `obtener_telemetria_por_mac` (`app/crud.py:31-40`) joins `telemetria` to `artefactos` on `id_artefacto` and filters by `mac`, then orders by `timestamp DESC` with a `LIMIT`. The `telemetria` table has no index on `(id_artefacto, timestamp)`. With partitioned tables, the optimizer must scan partitions.
- Files: `app/crud.py:31-40`, `schema_iot.sql:89-109`
- Cause: The DDL in `schema_iot.sql` only has the composite PK `(id, timestamp)` and no secondary index on `id_artefacto`. MariaDB will perform a full partition scan for each device query.
- Improvement path: Add `INDEX idx_telemetria_artefacto_ts (id_artefacto, timestamp)` to the `telemetria` table definition. Since this is a partitioned table, each partition needs this index (MariaDB handles this automatically for non-unique indexes).

### No pagination beyond `limite` parameter
- Problem: `GET /api/telemetria/{mac_dispositivo}` fetches up to 50 (or user-specified) records with no offset/cursor. There is no way to paginate through historical data.
- Files: `app/main.py:37`, `app/crud.py:31-40`
- Cause: The endpoint only supports a `limite` query parameter. No `offset` or `before` cursor.
- Improvement path: Add an `after` timestamp parameter or `offset` for cursor-based pagination.

### Synchronous MQTT publish blocks the async event loop
- Problem: `paho.mqtt.publish.single()` in `app/main.py:57` and `app/main.py:75` is a synchronous blocking call that runs directly in the FastAPI async handler. It performs DNS resolution, TCP connect, publish, and disconnect — all blocking I/O.
- Files: `app/main.py:57`, `app/main.py:75`
- Cause: Using the synchronous `publish.single()` API instead of the async-compatible `loop_start()` / `loop_stop()` pattern.
- Improvement path: Use `asyncio.to_thread()` to offload `publish.single()`, or better yet, use a persistent `Client` instance with `loop_start()` that connects once and reuses the connection.

## Fragile Areas

### Telemetria table has no foreign key on `id_artefacto`
- Files: `app/models.py:112`, `schema_iot.sql:91`
- Why fragile: MariaDB partitioning prohibits foreign keys on partitioned tables. This means `telemetria.id_artefacto` can reference an `artefacto.id` that doesn't exist, or an artefacto can be deleted leaving orphaned telemetry rows.
- Safe modification: If adding a new partition or modifying the schema, never add a FK to `telemetria.id_artefacto` unless you remove partitioning. Instead, enforce referential integrity at the application level (`app/crud.py:9-14` already validates device existence before insert).
- Test coverage: No tests exist for the orphan scenario.

### MQTT listener uses global mutable state
- Files: `app/mqtt_listener.py:9`
- Why fragile: `_main_loop` is a module-level global variable set during `iniciar_oyente_mqtt`. If the MQTT listener is restarted or if multiple workers run the same process, this global may point to a stale or wrong event loop.
- Safe modification: When refactoring, pass the event loop explicitly or use a class-based approach. Avoid multiple restarts of the MQTT client in the same process.
- Test coverage: None.

### Database URL contains password in connection string
- Files: `app/database.py:22`
- Why fragile: `DATABASE_URL` is constructed by string interpolation including `DB_PASSWORD`. If this URL is logged (e.g., `echo=True` on the engine), the password leaks into logs. Currently `echo=False` mitigates this, but someone debugging may flip it.
- Safe modification: Use SQLAlchemy's `create_async_engine` with `url` and `connect_args` separately, or ensure `echo=False` is always used in production. Consider logging a masked version of the URL.

### Device shadow fields (`estado_deseado`, `estado_reportado`) are defined but never used
- Files: `app/models.py:28-29`
- Why fragile: The `Artefacto` model has `estado_deseado` and `estado_reportado` columns (device shadow pattern), but no CRUD function or endpoint reads or writes them. The endpoints only deal with `is_encendido` and `is_online`. If these columns are meant for a future device-shadow implementation, they will likely conflict with the current `is_encendido` state.
- Safe modification: Document whether these are deprecated or planned. If deprecated, remove them. If planned, clarify the relationship with `is_encendido` before implementing.

## Scaling Limits

### MariaDB partitioning stops at 2026-12
- Current capacity: Partitions in `schema_iot.sql` cover `p202605` through `p202612`, plus a `p_max` catch-all.
- Limit: After December 2026, all new telemetry data lands in `p_max`. This partition will grow indefinitely, negating the benefits of partitioning (pruning, drop-partition for retention).
- Scaling path: Before January 2027, add new monthly partitions for 2027. Consider an automated partition management script (e.g., using `ALTER TABLE ... ADD PARTITION`). Also consider a scheduled job to drop old partitions for data retention.

### Single-threaded MQTT to async bridge
- Current capacity: One MQTT client thread delivers messages via `asyncio.run_coroutine_threadsafe` to the FastAPI event loop.
- Limit: Under high telemetry volume, the single `on_message` callback can become a bottleneck. If `procesar_payload` takes too long (DB latency), messages queue up in the MQTT client's receive buffer.
- Scaling path: Consider running multiple MQTT consumers, or using a message queue (Redis Streams / Kafka) between MQTT ingestion and DB writes.

### No connection pool tuning
- Current capacity: The SQLAlchemy `AsyncSessionLocal` sessionmaker uses default pool settings (`pool_size=5`, `max_overflow=10`).
- Limit: Under burst telemetry load from many ESP32 devices, the default pool size may cause connection waits or timeouts.
- Scaling path: Tune `pool_size` and `max_overflow` based on expected concurrent device count. Consider `pool_size=20, max_overflow=40` for 100+ devices.

## Dependencies at Risk

### No pinned dependency versions
- Risk: Without `requirements.txt` or `pyproject.toml`, any `pip install` pulls the latest versions of all packages. A breaking change in `paho-mqtt`, `sqlalchemy`, `aiomysql`, `fastapi`, or `pydantic` could take the service down.
- Impact: Unpredictable production failures.
- Migration plan: Generate `requirements.txt` from the current venv using `pip freeze`. Pin all versions. Add aCI step that installs from `requirements.txt`.

### `paho-mqtt` synchronous publish usage
- Risk: `paho.mqtt.publish.single()` is used in async FastAPI handlers (`app/main.py:57`, `app/main.py:75`), which blocks the event loop.
- Impact: Under slow MQTT broker response, all concurrent HTTP requests stall.
- Migration plan: Switch to `gmqtt` (native async) or run `publish.single()` with `asyncio.to_thread()`. Alternatively, use a shared `paho.mqtt.Client` instance managed in the lifespan.

## Missing Critical Features

### No authentication/authorization system
- Problem: The `app_api_keys` and `permisos_app_artefacto` tables and ORM models (`AppApiKey`, `PermisoAppArtefacto`) exist in the schema and models, but no endpoint or middleware validates API keys or enforces permissions. Every endpoint is fully open.
- Files: `app/models.py:6-16`, `app/models.py:47-56`, `schema_iot.sql:9-41`
- Blocks: Any multi-tenant or multi-app deployment. Also blocks production readiness.

### No telemetry aggregation or retention policy
- Problem: Telemetry is stored as raw rows with no aggregation, compaction, or retention. The only pruning mechanism is manual partition management.
- Files: `app/crud.py:31-40`, `schema_iot.sql:89-109`
- Blocks: Long-term analytics dashboards, storage cost control, and data retention compliance.

### No alerting on device disconnection
- Problem: The `conexion` handler (`app/mqtt_listener.py:56-66`) updates device state but does not create alerts. The `alertas_sistema` table and `AlertaSistema` model exist but are never written to.
- Files: `app/mqtt_listener.py:56-66`, `app/models.py:58-69`
- Blocks: Proactive notification when devices go offline, despite the schema being designed for it.

### No OTA deployment logic
- Problem: The `despliegues_ota` table and `DespliegueOta` model exist but have no CRUD operations, no endpoints, and no MQTT topics for triggering firmware updates.
- Files: `app/models.py:83-94`, `schema_iot.sql:67-76`
- Blocks: Remote firmware updates to ESP32 devices.

### No event logging
- Problem: The `eventos_usuario` table and `EventoUsuario` model exist but are never populated. User actions like relay toggles and limit changes are not audited.
- Files: `app/models.py:96-105`, `schema_iot.sql:79-86`
- Blocks: Audit trail, debugging, and compliance.

### No endpoint to create or register devices
- Problem: There is no REST endpoint to register a new `Artefacto`. Devices must be inserted directly into the database.
- Files: `app/main.py`, `app/crud.py`
- Blocks: Self-service device provisioning; requires DBA intervention to add devices.

## Test Coverage Gaps

### No automated tests exist
- What's not tested: All endpoints, CRUD operations, MQTT message handling, schema validation, error paths.
- Files: `app/main.py`, `app/crud.py`, `app/mqtt_listener.py`, `app/schemas.py`
- Risk: Any refactoring or feature addition may introduce regressions without detection. The `is_online` vs `is_encendido` bug is a direct result of this gap.
- Priority: High — at minimum, add unit tests for `app/crud.py` and integration tests for `app/main.py` endpoints.

### No test for the `conexion` handler bug
- What's not tested: The MQTT `conexion` handler at `app/mqtt_listener.py:56-66` is never tested. A unit test would have caught that `actualizar_estado_dispositivo` sets `is_encendido` instead of `is_online`.
- Files: `app/mqtt_listener.py:56-66`, `app/crud.py:42-55`
- Risk: This is a critical data corruption bug that has gone undetected due to lack of tests.
- Priority: Critical — this bug is actively corrupting device state.

### No test for edge cases in telemetry ingestion
- What's not tested: MAC mismatch between topic and payload (`app/mqtt_listener.py:49-51`), malformed JSON payloads, missing required fields, invalid numeric ranges.
- Files: `app/mqtt_listener.py:32-69`
- Risk: Invalid data could silently fail or partially insert.
- Priority: Medium

### No test for database constraint violations
- What's not tested: Duplicate MAC insertion, foreign key violations on `id_artefacto` (in non-partitioned tables), numeric range overflow on `DECIMAL(8,2)`.
- Files: `app/crud.py`, `app/schemas.py`
- Risk: Unexpected 500 errors from DB constraints that aren't handled gracefully.
- Priority: Medium

### Manual-only DB connectivity test
- What's not tested: `test_db.py` only validates that the DB connection works. It does not test schema correctness, CRUD operations, or any application logic.
- Files: `test_db.py`
- Risk: False confidence — DB may be reachable but schema may be wrong or queries may fail.
- Priority: Low (replaced once proper test suite exists)

---

*Concerns audit: 2026-05-12*