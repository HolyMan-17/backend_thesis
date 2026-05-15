# Frontend API Correction Sheet

> Pass this to the frontend agent. These are the changes from BACKEND-SPEC.md that need updating.

---

## Endpoint Path Changes

BACKEND-SPEC uses old flat paths. Backend now uses RESTful resource paths with MAC in URL.

### Changed

| BACKEND-SPEC (old) | New |
|---|---|
| `POST /api/comando/estado` (MAC in body) | `POST /api/dispositivos/{mac}/comando/estado` (MAC in path) |
| `POST /api/comando/limites` (MAC in body) | `POST /api/dispositivos/{mac}/comando/limites` (MAC in path) |
| `GET /api/dispositivos/{mac}/estado` | `GET /api/dispositivos/{mac}` (merged into device detail) |
| `GET /api/telemetria/{mac}` | `GET /api/dispositivos/{mac}/telemetria` |

### New (not in BACKEND-SPEC)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/dispositivos` | Register device (MAC only) |
| `GET` | `/api/dispositivos/{mac}` | Device detail (replaces `/estado`) |
| `PATCH` | `/api/dispositivos/{mac}` | Update device name/priority/limits |

### Unchanged

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | No auth |
| `POST` | `/api/telemetria` | Debug/bulk (API key auth, not JWT) |
| `POST` | `/api/users/sync` | Auth0 webhook (shared-secret auth) |

---

## Schema Changes

### `POST /api/dispositivos/{mac}/comando/estado`

**Before (BACKEND-SPEC):**
```json
{ "mac_dispositivo": "AA:BB:CC:DD:EE:FF", "encendido": true }
```

**After:**
```json
{ "encendido": true }
```

MAC is in the URL path. `mac_dispositivo` removed from body.

### `POST /api/dispositivos/{mac}/comando/limites`

**Before (BACKEND-SPEC):**
```json
{ "mac_dispositivo": "AA:BB:CC:DD:EE:FF", "limite_voltaje": 14.0 }
```

**After:**
```json
{ "limite_voltaje": 14.0 }
```

MAC is in the URL path. `mac_dispositivo` removed from body.

### `GET /api/dispositivos/{mac}` — Device Detail (NEW)

Replaces `GET /api/dispositivos/{mac}/estado`. Returns full device info, not just `is_online`:

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

Note: includes `id` and `nivel_acceso` (not in original BACKEND-SPEC list response).

### `GET /api/dispositivos` — Device List (UNCHANGED shape)

Same JSON shape as device detail, but as an array. Filtered by `permisos_usuario_artefacto`.

### `GET /api/dispositivos/{mac}/telemetria` — Telemetry (NEW path)

**Before:** `GET /api/telemetria/{mac_dispositivo}?limite=50`
**After:** `GET /api/dispositivos/{mac}/telemetria?limite=50`

Response shape unchanged except: `id_artefacto` replaced by `mac_dispositivo`.

### `POST /api/dispositivos` — Register Device (NEW)

Request:
```json
{ "mac": "00:1B:44:11:3A:B7" }
```

Response: `201` with full `DispositivoResponse` (same shape as detail).

### `PATCH /api/dispositivos/{mac}` — Update Device (NEW)

Request (partial, `exclude_unset=True`):
```json
{ "nombre_personalizado": "Kitchen Light", "limite_consumo_w": 150.0 }
```

Response: `200` with full `DispositivoResponse`.

---

## Error Response Format

**Before (BACKEND-SPEC):** FastAPI default `{"detail": "message"}`

**After:** Structured JSON:
```json
{
  "error": "not_found",
  "message": "Dispositivo no encontrado",
  "mac": "00:1B:44:11:3A:B7"
}
```

| `error` code | HTTP | When |
|---|---|---|
| `unauthorized` | 401 | Missing/invalid JWT |
| `forbidden` | 403 | No device access |
| `not_found` | 404 | Device not found |
| `validation_error` | 422 | Bad input |
| `rate_limited` | 429 | Too many requests |

---

## Validation Bounds for Limits

| Field | Min | Max |
|---|---|---|
| `limite_voltaje` | 0.1 | 60.0 |
| `limite_corriente` | 0.1 | 30.0 |
| `limite_potencia` | 0.1 | 500.0 |

Reject `NaN`, `Infinity`, `-Infinity`, negative, non-numeric types. Return `422`.

---

## WebSocket

**Before (BACKEND-SPEC):** `ws://localhost:8000/ws/telemetry`
**After:** `wss://api.thesisbroker.com/ws/telemetry?token=<access_token>`

- JWT as query param, not header
- TLS mandatory in production
- Close code `4001` for auth failures
- Events filtered to user's devices