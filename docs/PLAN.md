# SmartSaver IoT Backend — Implementation Plan

> Merged from grilled Q&A sessions and BACKEND-SPEC.md. Last updated 2026-05-12.

---

## Decision Table

| # | Decision | Choice |
|---|---|---|
| 1 | Actors & tenancy | Single user |
| 2 | Auth tables | New `usuarios` + `permisos_usuario_artefacto`; deprecate `app_api_keys` + `permisos_app_artefacto` |
| 3 | API authentication | OAuth2.1 + JWT via Auth0 (external IdP) + PKCE |
| 4 | IdP | Auth0 (domain: `thesisbroker.us.auth0.com`) |
| 5 | REST API auth | JWT Bearer via Auth0 JWKS for all `/api/*` except `/api/users/sync` and `POST /api/telemetria` |
| 6 | MQTT listener bug | Fix now — dedicated `actualizar_online_dispositivo()` |
| 7 | Hardcoded credentials | Unify to env vars (`MQTT_USER`, `MQTT_PASS`) |
| 8 | Frontend gaps | Device list, detail, provisioning, ID cleanup — all needed |
| 9 | Provisioning flow | LoRa pairing → gateway `POST /api/dispositivos` via REST |
| 10 | Provisioning transport | REST for registration |
| 11 | Telemetry response | Swap `id_artefacto` → `mac_dispositivo` |
| 12 | Test strategy | Unit + integration (pytest + Docker MariaDB) |
| 13 | Device shadow | Deferred until AI agent is built |
| 14 | Dependency management | `requirements.txt` (pin current versions) |
| 15 | Partition maintenance | Ignore for now (7 months of headroom) |
| 16 | Device detail fields | Active fields + `id` + `nivel_acceso` (no shadow columns) |
| 17 | Device list shape | Full detail per device (no pagination) |
| 18 | Provisioning payload | MAC only, defaults for everything else |
| 19 | Device update endpoint | `PATCH /api/dispositivos/{mac}` |
| 20 | MQTT ACK topics | Add `reporte/estado` and `reporte/limites` now |
| 21 | Telemetry REST endpoint | Keep both REST and MQTT paths |
| 22 | `app/__init__.py` | Add empty file |
| 23 | API path convention | RESTful resources (MAC in path, not body) |
| 24 | Full RESTful path map | Confirmed (see below) |
| 25 | Command schemas | Remove `mac_dispositivo` from body (MAC from path) |
| 26 | Error format | Structured JSON with machine-readable error codes |
| 27 | MQTT fix function | Dedicated `actualizar_online_dispositivo()` (not kwarg on existing fn) |
| 28 | Telemetry POST auth | API key auth (M2M), not JWT |
| 29 | Device claiming | Gateway provisioning first; user claim endpoint deferred |
| 30 | WebSocket auth | JWT as query param on `wss://` connection |

---

## Architecture Context

### Actors

- **ESP32 gateway** — central node, connects peripherals via LoRa, talks backend over MQTT + REST
- **Peripheral nodes** — sensors/actuators paired to gateway via LoRa; don't talk backend directly
- **Mobile app (frontend)** — single user, Auth0 auth, controls devices via REST API
- **Auth0** — external IdP (Authorization Code + PKCE flow)

### Provisioning Flow

1. User physically pairs peripheral to gateway (LoRa + secret exchange)
2. Gateway assigns ID, calls `POST /api/dispositivos` with peripheral's MAC
3. Backend creates `artefactos` row + `permisos_usuario_artefacto` entry
4. Frontend edits device name/priority/limits via `PATCH`
5. Telemetry flows via MQTT once peripheral reports through gateway

### Auth Flow

1. App redirects to Auth0 login (PKCE)
2. Auth0 returns `access_token` (JWT)
3. App sends `Authorization: Bearer <token>` on all `/api/*` requests
4. Backend validates JWT against Auth0 JWKS (`https://thesisbroker.us.auth0.com/.well-known/jwks.json`)
5. Auth0 Post-Login Action calls `POST /api/users/sync` → upserts `usuarios` row
6. Device access checked via `permisos_usuario_artefacto` JOIN

---

## Database Changes

### New: `usuarios`

```sql
CREATE TABLE usuarios (
    id INT AUTO_INCREMENT PRIMARY KEY,
    auth0_id VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) NOT NULL,
    nombre VARCHAR(255),
    fecha_registro DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ultimo_acceso DATETIME NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uc_auth0_id UNIQUE (auth0_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### New: `permisos_usuario_artefacto`

```sql
CREATE TABLE permisos_usuario_artefacto (
    id_usuario INT NOT NULL,
    id_artefacto INT NOT NULL,
    nivel_acceso VARCHAR(20) NOT NULL DEFAULT 'ADMIN',
    fecha_asignacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id_usuario, id_artefacto),
    CONSTRAINT fk_permiso_usuario FOREIGN KEY (id_usuario) REFERENCES usuarios(id) ON DELETE CASCADE,
    CONSTRAINT fk_permiso_artefacto FOREIGN KEY (id_artefacto) REFERENCES artefactos(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### Modified: `eventos_usuario`

```sql
ALTER TABLE eventos_usuario 
    ADD COLUMN id_usuario INT NULL AFTER id_artefacto,
    ADD CONSTRAINT fk_eventos_usuario FOREIGN KEY (id_usuario) REFERENCES usuarios(id) ON DELETE SET NULL;
```

### Deprecated

- `app_api_keys` — keep table, stop creating rows. M2M telemetry auth later.
- `permisos_app_artefacto` — replaced by `permisos_usuario_artefacto`. Keep during migration.
- `credenciales_mtls` — not wired. TODO marker.

---

## Environment Variables

Add to `.env`:

```env
AUTH0_DOMAIN=thesisbroker.us.auth0.com
AUTH0_AUDIENCE=https://api.thesisbroker.com
AUTH0_ISSUER=https://thesisbroker.us.auth0.com/
AUTH0_JWKS_URI=https://thesisbroker.us.auth0.com/.well-known/jwks.json
BACKEND_SYNC_SECRET=<generated-secret>
```

Existing `DB_*` and `MQTT_*` vars unchanged.

---

## RESTful API — Complete Path Map

### Active endpoints

```
GET    /health                                    — no auth
POST   /api/users/sync                            — shared-secret auth
GET    /api/dispositivos                          — JWT, read:devices
POST   /api/dispositivos                          — JWT, write:devices
GET    /api/dispositivos/{mac}                    — JWT, read:devices
PATCH  /api/dispositivos/{mac}                    — JWT, write:devices
POST   /api/dispositivos/{mac}/comando/estado     — JWT, write:devices
POST   /api/dispositivos/{mac}/comando/limites    — JWT, write:devices
GET    /api/dispositivos/{mac}/telemetria          — JWT, read:devices
POST   /api/telemetria                            — API key auth (M2M)
WS     /ws/telemetry                              — JWT query param
```

### Removed endpoints

```
GET    /api/telemetria/{mac}                      → /api/dispositivos/{mac}/telemetria
POST   /api/comando/estado                        → /api/dispositivos/{mac}/comando/estado
POST   /api/comando/limites                       → /api/dispositivos/{mac}/comando/limites
GET    /api/dispositivos/{mac}/estado              → merged into GET /api/dispositivos/{mac}
```

---

## Schemas

### New

| Schema | Fields | Notes |
|---|---|---|
| `DispositivoCreate` | `mac: str` (len=17, regex `^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$`) | Gateway provisioning |
| `DispositivoUpdate` | `nombre_personalizado?: str`, `nivel_prioridad?: str`, `limite_consumo_w?: float` | Partial, `exclude_unset=True` |
| `DispositivoResponse` | `id: int`, `mac: str`, `nombre_personalizado: str \| null`, `nivel_prioridad: str`, `limite_consumo_w: float`, `is_online: bool`, `is_encendido: bool`, `nivel_acceso: str`, `last_seen_at: datetime \| null` | |
| `ComandoEstado` | `encendido: bool` | MAC from path |
| `ComandoLimites` | `limite_voltaje?: float`, `limite_corriente?: float`, `limite_potencia?: float` | MAC from path |
| `UserSyncRequest` | `auth0_id: str`, `email: str`, `nombre?: str` | Auth0 webhook |
| `ErrorResponse` | `error: str`, `message: str`, `+context` | |

### Validation bounds for `ComandoLimites`

| Field | Min | Max |
|---|---|---|
| `limite_voltaje` | 0.1 | 60.0 |
| `limite_corriente` | 0.1 | 30.0 |
| `limite_potencia` | 0.1 | 500.0 |

Reject `NaN`, `Infinity`, `-Infinity`, negative, non-numeric. Return `422`.

### Modified

| Schema | Change |
|---|---|
| `TelemetriaResponse` | Replace `id_artefacto` with `mac_dispositivo` |

### Removed

| Schema | Reason |
|---|---|
| `DispositivoEstado` | MAC from path. Replaced by `ComandoEstado` |
| `DispositivoEstadoResponse` | Merged into `DispositivoResponse` |
| `DispositivoLimites` | MAC from path. Replaced by `ComandoLimites` |

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

## WebSocket

### `WS /ws/telemetry`

- URL: `wss://api.thesisbroker.com/ws/telemetry?token=<access_token>`
- JWT validated on connect
- Invalid/expired → close `4001` `"Unauthorized"`
- Token expiry during connection → close `4001`
- Filter events to user's devices via `permisos_usuario_artefacto`
- TLS mandatory in production

---

## Bug Fixes

### 1. MQTT `conexion` handler

**Problem:** Sets `is_encendido` instead of `is_online`.

**Fix:** Add dedicated `actualizar_online_dispositivo(db, mac, online: bool)` in `crud.py`. Call from `mqtt_listener.py` `conexion` handler.

### 2. Hardcoded MQTT credentials

**Fix:** Centralize config in `app/config.py`. Remove hardcoded values from `main.py` and `mock_esp32.py`.

---

## Rate Limiting

`slowapi` dependency. JWT `sub` claim as key (IP for sync endpoint):

| Endpoint | Limit |
|---|---|
| `POST .../comando/estado` | 10/min per user |
| `POST .../comando/limites` | 10/min per user |
| `GET .../telemetria` | 60/min per user |
| `GET /api/dispositivos` | 30/min per user |
| `POST /api/users/sync` | 5/min per IP |

---

## Structural Changes

### Add files

- `app/__init__.py` — empty
- `requirements.txt` — pinned versions
- `app/config.py` — centralized config via `pydantic-settings`
- `app/auth.py` — JWT validation, scope checking, user lookup
- `app/exceptions.py` — structured error response handler

### New dependencies

```
python-jose[cryptography]
pydantic-settings
slowapi
```

### Existing dependencies (pin)

```
fastapi==0.136.1
uvicorn==0.46.0
sqlalchemy==2.0.49
aiomysql==0.3.2
pymysql==1.1.3
paho-mqtt==2.1.0
python-dotenv==1.2.2
pydantic==2.13.3
```

### Modify files

- `app/main.py` — new endpoints, remove old paths, JWT middleware, rate limiting, env config, WS
- `app/models.py` — add `Usuario`, `PermisoUsuarioArtefacto`; add `id_usuario` to `EventoUsuario`; TODO on deprecated tables
- `app/crud.py` — add dispositivo CRUD, `actualizar_online_dispositivo`, user sync, authz check
- `app/schemas.py` — new schemas, remove old, swap `id_artefacto` → `mac_dispositivo`, validation bounds
- `app/mqtt_listener.py` — fix `conexion` handler, add `reporte/` subscriptions, update `is_encendido` on ACK
- `schema_iot.sql` — add DDL for `usuarios`, `permisos_usuario_artefacto`, ALTER `eventos_usuario`

---

## Device Provisioning Defaults

When `POST /api/dispositivos` receives a MAC:

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

Auto-create `permisos_usuario_artefacto` row for the authenticated user with `nivel_acceso='ADMIN'`.

---

## Device Detail Response Shape

```json
{
  "id": 1,
  "mac": "00:1B:44:11:3A:B7",
  "nombre_personalizado": "Kitchen Light",
  "nivel_prioridad": "alta",
  "limite_consumo_w": 150.00,
  "is_online": true,
  "is_encendido": false,
  "nivel_acceso": "ADMIN",
  "last_seen_at": "2026-05-12T14:30:00Z"
}
```

Shadow columns (`estado_deseado`, `estado_reportado`, `override_activo`, `vencimiento_lease`) excluded until AI agent.

---

## Error Response Format

All errors use structured JSON:

```json
{
  "error": "not_found",
  "message": "Dispositivo no encontrado",
  "mac": "00:1B:44:11:3A:B7"
}
```

| `error` code | HTTP status | When |
|---|---|---|
| `unauthorized` | 401 | Missing/invalid JWT |
| `forbidden` | 403 | User lacks device access |
| `not_found` | 404 | Device/MAC not found |
| `validation_error` | 422 | Invalid input |
| `rate_limited` | 429 | Too many requests |
| `sync_unauthorized` | 401 | Invalid sync secret |

---

## Deferred Items

| Item | Reason | Revisit when |
|---|---|---|
| Device shadow | AI agent not built | AI development begins |
| AI conflict resolution | Depends on shadow | AI development begins |
| Partition maintenance | Partitions through 2026-12 | Q4 2026 |
| Pagination on device list | <20 devices | Device count grows |
| `POST /api/dispositivos/claim` | User claiming flow | Multi-device management |
| `app_api_keys` M2M auth | Single source; telemetry via MQTT | M2M throughput needs |
| Device deletion | Not needed | Lifecycle management |
| Telemetry cleanup | Not needed | Storage growth concern |