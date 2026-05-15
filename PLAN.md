# SmartSaver IoT Backend — Implementation Plan

> Grilled and validated on 2026-05-12. Every decision below was resolved through direct Q&A.

---

## Decision Table

| # | Decision | Choice |
|---|---|---|
| 1 | Actors & tenancy | Single user |
| 2 | Unused auth tables | Keep in code, gate behind TODO markers |
| 3 | API authentication | OAuth2.1 + JWT via Auth0 (external IdP) + PKCE |
| 4 | IdP | Auth0 |
| 5 | REST API auth | JWT Bearer token validation via Auth0 JWKS |
| 6 | MQTT listener bug | Fix now — add `actualizar_online_dispositivo()` |
| 7 | Hardcoded credentials | Unify to env vars (`MQTT_USER`, `MQTT_PASS`) |
| 8 | Frontend gaps | All four needed: device list, detail, provisioning, ID cleanup |
| 9 | Provisioning flow | LoRa pairing → gateway `POST /api/dispositivos` via REST |
| 10 | Provisioning transport | REST (not MQTT) for registration |
| 11 | Telemetry response | Swap `id_artefacto` for `mac_dispositivo` |
| 12 | Test strategy | Unit + integration (pytest + Docker MariaDB) |
| 13 | Device shadow | Deferred until AI agent is built |
| 14 | Dependency management | `requirements.txt` (pin current versions) |
| 15 | Partition maintenance | Ignore for now (7 months of headroom) |
| 16 | Device detail fields | Active fields only (no shadow columns) |
| 17 | Device list shape | Full detail per device (no pagination) |
| 18 | Provisioning payload | MAC only, defaults for everything else |
| 19 | Device update endpoint | `PATCH /api/dispositivos/{mac}` |
| 20 | MQTT ACK topics | Add `reporte/estado` and `reporte/limites` now |
| 21 | Telemetry REST endpoint | Keep both REST and MQTT paths |
| 22 | `app/__init__.py` | Add empty file |
| 23 | API path convention | RESTful resources (breaking change from current paths) |
| 24 | Full RESTful path map | Confirmed (see below) |
| 25 | Command schemas | Remove `mac_dispositivo` from body (MAC from path) |
| 26 | Error format | Structured JSON with machine-readable error codes |
| 27 | MQTT fix function | Dedicated `actualizar_online_dispositivo()` |

---

## Architecture Context

### Actors

- **ESP32 gateway** — central node that connects peripherals via LoRa and communicates with the backend over MQTT + REST
- **Peripheral nodes** — sensors/actuators paired to the gateway via LoRa (secret exchange); they don't talk to the backend directly
- **Mobile app (frontend)** — single user, authenticates via Auth0, controls devices through REST API

### Provisioning Flow

1. User physically pairs peripheral to gateway (LoRa + secret exchange)
2. Gateway assigns an ID to the peripheral and calls `POST /api/dispositivos` with the peripheral's MAC
3. Backend creates the `artefactos` row in MariaDB with defaults
4. Frontend can then edit the device name, priority, and consumption limits via `PATCH`
5. Telemetry begins flowing via MQTT once the peripheral reports data through the gateway

### Single-User Constraints

- No multi-tenant isolation needed for now
- `app_api_keys`, `permisos_app_artefacto`, and `credenciales_mtls` tables remain in the schema but are **not wired** (marked with TODO references)
- Auth is a single JWT-validated user scope via Auth0

---

## RESTful API — Complete Path Map

### New endpoints

```
GET    /api/dispositivos                          — list all devices
POST   /api/dispositivos                          — register device (MAC only)
GET    /api/dispositivos/{mac}                    — device detail
PATCH  /api/dispositivos/{mac}                    — update device settings
POST   /api/dispositivos/{mac}/comando/estado     — toggle relay
POST   /api/dispositivos/{mac}/comando/limites    — update limits
GET    /api/dispositivos/{mac}/telemetria          — get telemetry for device
POST   /api/telemetria                            — debug / bulk insert (keep)
GET    /health                                    — health check (unchanged)
```

### Removed endpoints

```
GET    /api/telemetria/{mac}                      → moved to /api/dispositivos/{mac}/telemetria
POST   /api/comando/estado                        → moved under /api/dispositivos/{mac}/comando/estado
POST   /api/comando/limites                       → moved under /api/dispositivos/{mac}/comando/limites
GET    /api/dispositivos/{mac}/estado              → merged into GET /api/dispositivos/{mac}
```

---

## Schemas

### New

| Schema | Fields |
|---|---|
| `DispositivoCreate` | `mac: str` (len=17) |
| `DispositivoUpdate` | `nombre_personalizado?: str`, `nivel_prioridad?: str`, `limite_consumo_w?: float` |
| `DispositivoResponse` | `mac, nombre_personalizado, nivel_prioridad, limite_consumo_w, is_online, is_encendido, last_seen_at` |
| `ComandoEstado` | `encendido: bool` |
| `ComandoLimites` | `limite_voltaje?: float`, `limite_corriente?: float`, `limite_potencia?: float` |
| `ErrorResponse` | `error: str`, `message: str`, `+context fields` |

### Modified

| Schema | Change |
|---|---|
| `TelemetriaResponse` | Replace `id_artefacto` with `mac_dispositivo` |

### Removed

| Schema | Reason |
|---|---|
| `DispositivoEstado` | MAC now comes from path, not body. Replaced by `ComandoEstado` |
| `DispositivoEstadoResponse` | Merged into `DispositivoResponse` |
| `DispositivoLimites` | MAC now from path. Replaced by `ComandoLimites` |

---

## MQTT Topics

### Subscribe (backend)

| Topic | Purpose |
|---|---|
| `smartups/dispositivos/{mac}/telemetria` | Device telemetry data |
| `smartups/dispositivos/{mac}/conexion` | Birth/LWT reachability |
| `smartups/dispositivos/{mac}/reporte/estado` | **NEW** — device confirms relay state |
| `smartups/dispositivos/{mac}/reporte/limites` | **NEW** — device confirms limit update |

### Publish (backend → device)

| Topic | Purpose |
|---|---|
| `smartups/dispositivos/{mac}/comando/estado` | Turn relay on/off |
| `smartups/dispositivos/{mac}/comando/limites` | Push new operational limits |

---

## Bug Fixes

### 1. MQTT `conexion` handler (`mqtt_listener.py:63`)

**Problem:** The `conexion` handler calls `actualizar_estado_dispositivo(db, mac, encendido=estado_bool)` which sets `is_encendido` (relay state). The intent is to set `is_online` (network reachability).

**Fix:**
- Add `actualizar_online_dispositivo(db, mac, online: bool)` in `crud.py`
- Update `mqtt_listener.py` to call the new function in the `conexion` handler
- `actualizar_estado_dispositivo` continues to set `is_encendido` for relay commands only

### 2. Hardcoded MQTT credentials (`main.py:57,74`)

**Problem:** Publishing commands use literal `esp-gateway` / `wUbcJJiZcLqV3dDo2r9e` while the listener reads from env vars.

**Fix:**
- Extract MQTT credentials into `app/config.py` (or just read `os.getenv` in `main.py`)
- Use `MQTT_USER` and `MQTT_PASS` env vars consistently
- Remove hardcoded values from `main.py` and `mock_esp32.py`

---

## Structural Changes

### Add files

- `app/__init__.py` — empty, makes package explicit
- `requirements.txt` — pinned versions of all dependencies
- `app/config.py` — centralized config loading (DB, MQTT, Auth0 settings)
- `app/exceptions.py` — structured error response handler

### Modify files

- `app/main.py` — new endpoints, remove old paths, env-var MQTT creds, Auth0 middleware
- `app/crud.py` — add CRUD functions for dispositivos, add `actualizar_online_dispositivo`
- `app/schemas.py` — add new schemas, remove old ones, swap `id_artefacto` → `mac_dispositivo`
- `app/mqtt_listener.py` — fix `conexion` handler, add `reporte/estado` and `reporte/limites` subscriptions
- `app/models.py` — add TODO markers to unused auth tables
- `app/database.py` — no changes expected

---

## Authentication Plan

### Auth0 Integration

- **Flow:** Authorization Code with PKCE (mobile app is a public client)
- **Backend role:** Validate JWTs, not issue them
- **Middleware:** FastAPI dependency that verifies `Authorization: Bearer <token>` against Auth0's JWKS endpoint
- **Scope:** Single-user, single scope (`access:api` or similar)
- **New dependency:** `python-jose[cryptography]` or `PyJWT` for JWT validation

### Endpoints requiring auth

All endpoints except `GET /health` will require a valid JWT.

### MQTT auth

ESP32 gateway continues using MQTT username/password. This is transport-level auth separate from the JWT system.

---

## Test Plan

### Framework

- **pytest** with `pytest-asyncio` for async tests
- **httpx` `AsyncClient`** for FastAPI endpoint tests
- **Docker MariaDB** for integration tests (test against real schema, real partitioning)
- Mock MQTT publisher for command tests

### What to test

1. **Endpoint tests** — every new endpoint (CRUD, commands, telemetry)
2. **Schema validation** — malformed requests, missing fields, invalid MACs
3. **Auth middleware** — missing token, expired token, invalid signature
4. **CRUD tests** — create device with MAC only, update partial fields, query by MAC
5. **MQTT handler test** — verify `conexion` sets `is_online` (not `is_encendido`)
6. **Integration** — telemetry readback after MQTT insert, partition-aware queries

---

## Device Provisioning Defaults

When `POST /api/dispositivos` receives just a MAC:

| Column | Default |
|---|---|
| `mac` | from request |
| `nombre_personalizado` | `null` |
| `nivel_prioridad` | `"normal"` |
| `limite_consumo_w` | `0` |
| `is_online` | `false` |
| `is_encendido` | `false` |
| `estado_deseado` | `false` |
| `estado_reportado` | `false` |
| `last_seen_at` | `null` |
| `override_activo` | `false` |
| `vencimiento_lease` | `null` |

---

## Device Detail Response Shape

`GET /api/dispositivos/{mac}` and `GET /api/dispositivos` return:

```json
{
  "mac": "00:1B:44:11:3A:B7",
  "nombre_personalizado": "Kitchen Light",
  "nivel_prioridad": "alta",
  "limite_consumo_w": 150.00,
  "is_online": true,
  "is_encendido": false,
  "last_seen_at": "2026-05-12T14:30:00Z"
}
```

Shadow columns (`estado_deseado`, `estado_reportado`, `override_activo`, `vencimiento_lease`) are **excluded** until the shadow pattern and AI agent are implemented.

---

## Error Response Format

All error responses follow this shape:

```json
{
  "error": "not_found",
  "message": "Dispositivo no encontrado",
  "mac": "00:1B:44:11:3A:B7"
}
```

- `error` — machine-readable code (`not_found`, `validation_error`, `unauthorized`, etc.)
- `message` — human-readable (Spanish is fine, matches project convention)
- Additional context fields vary by endpoint

---

## Deferred Items

| Item | Reason | Revisit when |
|---|---|---|
| Device shadow (`estado_deseado`/`estado_reportado`) | AI agent not yet built | AI agent development begins |
| AI conflict resolution / `eventos_usuario` wiring | Depends on shadow pattern | AI agent development begins |
| Partition maintenance for `telemetria` | Partitions pre-created through 2026-12 | Q4 2026 |
| Multi-tenancy / API key auth | Single-user for now | Multi-user requirement arises |
| Pagination on device list | <20 devices expected | Device count grows large |