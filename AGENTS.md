# SmartSaver IoT Backend — Agent Notes

## Quick Commands

```bash
# Start/restart the API server (systemd)
sudo systemctl restart fastapi_iot
sudo systemctl status fastapi_iot
sudo journalctl -u fastapi_iot -f   # live logs

# Dev mode (from project root iot_backend/)
source venv/bin/activate
uvicorn app.main:app --reload

# Install dependencies
pip install -r requirements.txt

# Test DB connectivity
python test_db.py

# Simulate an ESP32 device publishing telemetry
python -m app.mock_esp32

# Run DB migration V5 → V6 (as admin)
sudo mysql iot_telemetry < migration_v6.sql
```

## Repository Structure

- `app/main.py` — FastAPI app entrypoint, all REST endpoints
- `app/config.py` — Centralized settings via `pydantic-settings` (DB, MQTT, Auth0)
- `app/database.py` — Async SQLAlchemy engine + session factory
- `app/models.py` — ORM models (Spanish table/column names)
- `app/schemas.py` — Pydantic request/response schemas
- `app/crud.py` — Async DB operations
- `app/auth.py` — Auth0 JWT validation + user sync secret verification
- `app/exceptions.py` — Structured error response handler
- `app/mqtt_listener.py` — MQTT subscriber (background thread via FastAPI lifespan)
- `app/ws_manager.py` — WebSocket connection manager (device-filtered broadcasts)
- `app/mock_esp32.py` — Standalone ESP32 simulator script
- `app/__init__.py` — Package marker (empty)
- `schema_iot.sql` — MariaDB DDL V5.0 (run once to bootstrap)
- `migration_v3.sql` — Idempotent migration V2.1 → V3.0 (adds Auth0 tables)
- `migration_v4.sql` — Idempotent migration V3.0 → V4.0 (soft delete, persistent limits, alert resolution)
- `migration_v5.sql` — Idempotent migration V4.0 → V5.0 (limits normalized to `artefactos_limites`, device shadow consolidated)
- `requirements.txt` — Pinned dependencies
- `scripts/seed_test_device.py` — Seed test device + user permission

## Dependencies

Managed via `requirements.txt`. Key packages:

- fastapi, uvicorn, sqlalchemy, aiomysql, pymysql, paho-mqtt, python-dotenv, pydantic
- python-jose[cryptography] — JWT validation (Auth0)
- pydantic-settings — Centralized config from env vars
- slowapi — Rate limiting (installed, not yet wired)
- httpx — Async HTTP client for JWKS fetching

Install: `pip install -r requirements.txt`

## Environment

Requires `.env` in project root with:

```env
# Database
DB_USER=
DB_PASSWORD=
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=

# MQTT (no defaults — must be set in env)
MQTT_USER=
MQTT_PASS=
MQTT_HOST=127.0.0.1
MQTT_PORT=1883

# Auth0
AUTH0_DOMAIN=thesisbroker.us.auth0.com
AUTH0_AUDIENCE=https://api.thesisbroker.com
AUTH0_ISSUER=https://thesisbroker.us.auth0.com/
AUTH0_JWKS_URI=https://thesisbroker.us.auth0.com/.well-known/jwks.json

# Shared secret for Auth0 user sync webhook
BACKEND_SYNC_SECRET=
```

Local Mosquitto broker must be running at `127.0.0.1:1883`.

## Critical Architecture Notes

### Spanish naming throughout
Models, tables, columns, and endpoint paths use Spanish. Key translations:
- `artefacto` = device/appliance
- `telemetria` = telemetry
- `encendido` = turned on (relay state)
- `conexion` = connection
- `nombre_personalizado` = personalized name (user-assigned device label)
- `alerta` = alert/notification
- `evento` = event (audit log entry)
- `agregado` = aggregate/time-bucketed summary

### Device Shadow (`estado_deseado` vs `estado_reportado`)
- `estado_deseado` = what the backend/commands want the relay to be
- `estado_reportado` = what the device ACKed back (source of truth for UI)
- User command via API sets `estado_deseado`, activates a 5-minute lease, then publishes MQTT
- Device ACK via `.../reporte/estado` updates `estado_reportado`
- Frontend should show `estado_reportado` as current state; `estado_deseado != estado_reportado` can be shown as "syncing"

### `is_online` vs relay state — do not confuse
- `is_online` = device network reachability (set by MQTT birth/LWT messages)
- `estado_reportado` = physical relay on/off state (set by device ACK)
- The `conexion` MQTT handler must use `actualizar_online_dispositivo()`, NOT `actualizar_estado_reportado()`

### Config centralized in `app/config.py`
All env vars loaded via `pydantic-settings`. Do not use `os.getenv()` directly in other modules — import from `app.config import settings`.

### No hardcoded MQTT credentials
MQTT credentials come from `MQTT_USER` and `MQTT_PASS` env vars. No hardcoded values in `main.py` or `mock_esp32.py`.

### MariaDB partitioning constraint on `telemetria`
- Composite primary key `(id, timestamp)` — required for range partitioning by month
- `id_artefacto` intentionally has **no foreign key** (MariaDB partitioning limitation)
- Partitions are pre-created through 2026–12 with a `p_max` catch-all

### Auth0 JWT validation
All `/api/*` endpoints require JWT Bearer auth (except `GET /health` and `POST /api/users/sync`). `POST /api/telemetria` uses no auth (M2M). See `app/auth.py`.

### Soft delete (`deleted_at`)
- Devices are soft-deleted (`deleted_at = now()`), never hard-deleted
- All device queries filter `deleted_at IS NULL`
- Re-registering a deleted device restores it (`deleted_at = NULL`)
- Telemetry for deleted devices is rejected

### Personalized device names (`nombre_personalizado`)
- Editable only via `PATCH /api/dispositivos/{mac}` (not at registration time)
- Empty strings and whitespace-only strings rejected with 422 validation error
- Stripped of leading/trailing whitespace before storage
- `null` means no custom name set; frontend should fall back to MAC address
- Duplicate names allowed across devices (no uniqueness constraint)

### Priority levels (`nivel_prioridad`)
- Valid values: `P1` (highest), `P2` (medium/default), `P3` (lowest)
- New devices default to `P2` at provisioning time
- Validated application-level via Pydantic in `DispositivoUpdate`; invalid values return 422
- Filter devices by priority: `GET /api/dispositivos?prioridad=P1` (optional query param)
- Updated via `PATCH /api/dispositivos/{mac}` with `{"nivel_prioridad": "P1"}`

### Persistent device limits (`artefactos_limites`)
- Stored in normalized `artefactos_limites` table (1:1 with `artefactos`)
- Columns: `limite_consumo_w`, `limite_voltaje`, `limite_corriente`, `limite_potencia`
- Updated via `PATCH /api/dispositivos/{mac}` or `POST .../comando/limites` (persisted before MQTT publish)
- Also persisted when device reports limits via MQTT `.../reporte/limites`
- Used for threshold alert checking on incoming telemetry
- All device queries eagerly load the `limites` relationship to avoid N+1

### User lease arbitration (`override_activo`, `vencimiento_lease`)
- When user sends manual relay command, backend activates a 5-minute lease
- During lease: `override_activo = TRUE`, `NOW() < vencimiento_lease`
- AI/automated scheduler must check `verificar_lease_activo()` before issuing commands; skip if lease is active
- Safety override: voltage or current threshold alerts immediately break the lease (`romper_lease_por_seguridad`) and log a `safety_override` event
- Lease is NOT applied to limit commands — only relay state commands

### Threshold alerts (`alertas_sistema`)
- Checked automatically on every incoming telemetry MQTT message
- One active alert per `(device, alert_type)` — deduplicated until resolved
- Types: `sobretension`, `sobrecorriente`, `sobrepotencia`, `bms_critica`
- Alert auto-resolves when telemetry drops below threshold
- Voltage/current alerts break the user lease for safety
- `GET /api/alertas` lists alerts (active or all)
- `PATCH /api/alertas/{id}` marks alert as resolved

### Events (`eventos_usuario`)
- Audit trail for user actions and device state changes
- Logged actions: `comando_estado`, `reporte_estado`, `conexion_online`, `conexion_offline`, `safety_override`
- BMS emergency shutdown triggers a `safety_override` event with detailed reason
- `GET /api/eventos` lists events for user's devices

### Edge-AI BMS status (`ai_status`)
- Integer field on `telemetria` table: `0` = SAFE, `1` = RISKY, `2` = CRITICAL
- Sent by ESP32 in telemetry payload as `ai_status` (defaults to 0 if absent)
- Included in `POST /api/telemetria` and `GET /api/dispositivos/{mac}/telemetria` responses

### BMS emergency alerts (`alerta` MQTT topic)
- Subscribe: `smartups/dispositivos/{mac}/alerta`
- Payload: `{"alerta": "<message>", "ai_status": <0-2>}`
- On receipt: `emergencia_bms_shutdown()` forces both `estado_deseado` and `estado_reportado` to OFF, breaks any active user lease, creates a `bms_critica` alert (severity `"critica"`), logs a `safety_override` event, and broadcasts via WebSocket
- Deduplicated: only one active `bms_critica` alert per device at a time

### Telemetry aggregates (`GET /api/dispositivos/{mac}/agregados`)
- SQL GROUP BY with hour/day buckets
- Returns: `bucket`, `potencia_promedio_w`, `potencia_maxima_w`, `energia_wh`
- Default range: last 24h. Max range: 30 days
- Energy formula: `AVG(potencia) * bucket_hours` (approximate Wh)

### Structured error responses
All errors return `{error: str, message: str, ...context}`. Do not use FastAPI's default `{detail}`. Raise custom exceptions from `app/exceptions.py`.

### API path convention
RESTful resources with MAC in URL path:
- `GET /api/dispositivos` — list devices (optional `?prioridad=P1|P2|P3` filter)
- `POST /api/dispositivos` — register device
- `GET /api/dispositivos/{mac}` — device detail
- `PATCH /api/dispositivos/{mac}` — update device (name, priority, all limits)
- `DELETE /api/dispositivos/{mac}` — soft delete device
- `POST /api/dispositivos/{mac}/comando/estado` — toggle relay (activates 5min lease)
- `POST /api/dispositivos/{mac}/comando/limites` — update limits (persisted + MQTT)
- `GET /api/dispositivos/{mac}/telemetria` — get telemetry
- `GET /api/dispositivos/{mac}/agregados` — telemetry aggregates
- `GET /api/alertas` — list alerts
- `PATCH /api/alertas/{alerta_id}` — resolve alert
- `GET /api/eventos` — list events

Legacy paths still work during transition:
- `GET /api/telemetria/{mac_dispositivo}`
- `POST /api/comando/estado`
- `POST /api/comando/limites`
- `GET /api/dispositivos/{mac_dispositivo}/estado`

### MQTT topics
Subscribe: `smartups/dispositivos/{mac}/telemetria`, `.../conexion`, `.../reporte/estado`, `.../reporte/limites`, `.../provisionamiento`, `.../alerta`
Publish: `smartups/dispositivos/{mac}/comando/estado`, `.../comando/limites`

### WebSocket
`WS /ws/telemetry?token=<jwt>` — validates JWT, looks up user's devices, filters events to allowed MACs only.
- Pushes telemetry in real-time as devices publish via MQTT
- Pushes `conexion` events when device goes online/offline
- Invalid/expired token → close code `4001`
- Production MUST use `wss://`

## Testing

No test framework is configured yet. Verification methods:
- `python test_db.py` — checks MariaDB connectivity
- `python -m app.mock_esp32` — simulates full device lifecycle
- Start server and verify endpoints with curl/httpx

Planned: pytest + pytest-asyncio + httpx AsyncClient + Docker MariaDB for integration tests.

## Migration History

| Version | File | Changes |
|---|---|---|
| V2.1 → V3.0 | `migration_v3.sql` | Auth0 tables (`usuarios`, `permisos_usuario_artefacto`) |
| V3.0 → V4.0 | `migration_v4.sql` | Soft delete, persistent limits, alert resolution |
| V4.0 → V5.0 | `migration_v5.sql` | Limits normalized to `artefactos_limites`, `is_encendido` dropped, device shadow consolidated (`estado_deseado`/`estado_reportado`), lease arbitration added |
| V5.0 → V6.0 | `migration_v6.sql` | Adds `ai_status` integer column to `telemetria` (Edge-AI BMS classification: 0=SAFE, 1=RISKY, 2=CRITICAL) |
