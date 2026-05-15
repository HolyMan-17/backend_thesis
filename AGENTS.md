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

# Run DB migration (as admin)
sudo mysql iot_telemetry < migration_v3.sql
```

## Repository Structure

- `app/main.py` — FastAPI app entrypoint, all REST endpoints
- `app/config.py` — Centralized settings via `pydantic-settings` (DB, MQTT, Auth0)
- `app/database.py` — Async SQLAlchemy engine + session factory (reads from `config.py`)
- `app/models.py` — ORM models (Spanish table/column names)
- `app/schemas.py` — Pydantic request/response schemas
- `app/crud.py` — Async DB operations
- `app/auth.py` — Auth0 JWT validation + user sync secret verification
- `app/exceptions.py` — Structured error response handler
- `app/mqtt_listener.py` — MQTT subscriber (runs as background thread via FastAPI lifespan)
- `app/mock_esp32.py` — Standalone ESP32 simulator script
- `app/__init__.py` — Package marker (empty)
- `schema_iot.sql` — MariaDB DDL (run once to bootstrap the database)
- `migration_v3.sql` — Idempotent migration V2.1 → V3.0 (adds Auth0 tables)
- `requirements.txt` — Pinned dependencies

## Dependencies

Managed via `requirements.txt`. Key packages:

- fastapi, uvicorn, sqlalchemy, aiomysql, pymysql, paho-mqtt, python-dotenv, pydantic
- python-jose[cryptography] — JWT validation (Auth0)
- pydantic-settings — Centralized config from env vars
- slowapi — Rate limiting
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

# MQTT
MQTT_USER=esp-gateway
MQTT_PASS=change_me
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

### `is_encendido` vs `is_online` — do not confuse
- `is_online` = device network reachability (set by MQTT birth/LWT messages)
- `is_encendido` = physical relay on/off state (set by user commands or device ACK)
- The `conexion` MQTT handler must use `actualizar_online_dispositivo()`, NOT `actualizar_estado_dispositivo()`

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

### Structured error responses
All errors return `{error: str, message: str, ...context}`. Do not use FastAPI's default `{detail}`. Raise custom exceptions from `app/exceptions.py`.

### API path convention
RESTful resources with MAC in URL path:
- `GET /api/dispositivos` — list devices
- `POST /api/dispositivos` — register device
- `GET /api/dispositivos/{mac}` — device detail
- `PATCH /api/dispositivos/{mac}` — update device
- `POST /api/dispositivos/{mac}/comando/estado` — toggle relay
- `POST /api/dispositivos/{mac}/comando/limites` — update limits
- `GET /api/dispositivos/{mac}/telemetria` — get telemetry

Legacy paths still work during transition:
- `GET /api/telemetria/{mac}`
- `POST /api/comando/estado`
- `POST /api/comando/limites`
- `GET /api/dispositivos/{mac}/estado`

### MQTT topics
Subscribe: `smartups/dispositivos/{mac}/telemetria`, `.../conexion`, `.../reporte/estado`, `.../reporte/limites`
Publish: `smartups/dispositivos/{mac}/comando/estado`, `.../comando/limites`

### WebSocket
`WS /ws/telemetry?token=<jwt>` — validates JWT, accepts connection. Real-time telemetry streaming planned.

## Testing

No test framework is configured yet. The only verification is `test_db.py`, which checks MariaDB connectivity.

Planned: pytest + pytest-asyncio + httpx AsyncClient + Docker MariaDB for integration tests.