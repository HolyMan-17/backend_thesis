# Backend-Frontend Integration Verification Spec

> Use this document to verify the backend and frontend are wired and communicating correctly.
> This is a **post-implementation verification checklist**, not a design spec.
>
> **CRITICAL:** Read `INTEGRATION_VERIFY.md` and `CORRECTIONS.md` first.
> Device pairing is hardware-only — the app NEVER registers or claims devices.

---

## Table of Contents

1. [Environment & URLs](#1-environment--urls)
2. [Auth0 Flow](#2-auth0-flow)
3. [Integration Test Sequence](#3-integration-test-sequence)
4. [Endpoint Reference](#4-endpoint-reference)
5. [Error Contract](#5-error-contract)
6. [WebSocket](#6-websocket)
7. [Common Failures & Fixes](#7-common-failures--fixes)

---

## 1. Environment & URLs

| Component | Local Dev | Production |
|---|---|---|
| API Base | `http://localhost:8000` | `https://api.thesisbroker.com` |
| Auth0 Domain | `thesisbroker.us.auth0.com` | `thesisbroker.us.auth0.com` |
| Auth0 Audience | `https://api.thesisbroker.com` | `https://api.thesisbroker.com` |
| Mosquitto MQTT | `127.0.0.1:1883` | (AWS IoT Core or equivalent) |

**Headers for all authenticated requests:**
```
Authorization: Bearer <access_token>
Content-Type: application/json
```

**CORS origins (backend allows):**
- `http://localhost:19006` (Expo web dev)
- `http://localhost:3000` (web dev)
- `http://localhost:8081` (Expo dev client)
- `exp://127.0.0.1:8081` (Expo Go)
- `smartsaver://callback` (deep link)

---

## 2. Auth0 Flow

### 2.1 PKCE Login Sequence

1. **Frontend** initiates Auth0 login via PKCE (Auth0 React Native SDK)
2. **Auth0** redirects back with `code`
3. **Frontend** exchanges `code` for `access_token` + `id_token`
4. **Frontend** stores `access_token` in expo-secure-store
5. **Frontend** sends `access_token` as `Authorization: Bearer <token>` on every API call
6. **Auth0 Post-Login Action** calls `POST /api/users/sync` to create/update user in backend DB

### 2.2 Critical: User Sync Webhook

**The backend will reject ALL authenticated calls** (`403 Forbidden`) until the user exists in the `usuarios` table.

**Trigger:** Auth0 Post-Login Action calls the sync webhook after successful login/registration:

```bash
POST /api/users/sync
Authorization: Bearer <BACKEND_SYNC_SECRET>
Content-Type: application/json

{
  "auth0_id": "auth0|64f8a1b2c3d4e5f6a7b8c9d0",
  "email": "user@example.com",
  "nombre": "John Doe"
}
```

**Expected response:**
```json
{ "status": "synced", "auth0_id": "auth0|64f8a1b2c3d4e5f6a7b8c9d0" }
```

**To verify sync worked:**
```bash
mysql -u api_iot_user -p iot_telemetry -e "SELECT auth0_id, email, activo FROM usuarios;"
```

### 2.3 Token Requirements

- **Algorithm:** RS256
- **Audience:** `https://api.thesisbroker.com`
- **Issuer:** `https://thesisbroker.us.auth0.com/`
- **Required claim:** `sub` (Auth0 user ID)
- **JWKS URI:** `https://thesisbroker.us.auth0.com/.well-known/jwks.json`
- The backend caches JWKS for 1 hour

### 2.4 User Not Found → 403 Forbidden

If the JWT is valid but the user does not exist in the `usuarios` table (sync webhook not yet fired), the backend returns:

```json
{ "error": "forbidden", "message": "User not found or inactive", "auth0_id": "auth0|..." }
```

with HTTP **403**. The frontend should NOT retry — it should force logout and prompt re-login.

---

## 3. Integration Test Sequence

Run these in order. Each step depends on the previous one succeeding.

### Step 0: Health Check
```bash
GET /health
```
**Expected:** `200 OK`, empty JSON `{}`

**If this fails:** Backend is not running or wrong URL.

---

### Step 1: User Sync (Auth0 Webhook)
```bash
POST /api/users/sync
Authorization: Bearer <BACKEND_SYNC_SECRET>

{ "auth0_id": "<AUTH0_SUB>", "email": "test@example.com", "nombre": "Test User" }
```
**Expected:** `200 OK` with `{ "status": "synced", "auth0_id": "..." }`

**If this fails:** Wrong `BACKEND_SYNC_SECRET` in `.env` or missing header.

---

### Step 2: Authenticated User Check
```bash
GET /api/dispositivos
Authorization: Bearer <REAL_AUTH0_ACCESS_TOKEN>
```
**Expected:** `200 OK`, `[]` (empty array for new user)

**If 401:** Token invalid or expired.
**If 403:** User not synced — run Step 1 first.

---

### Step 3: Seed Device (Backend Admin / Hardware Process)

**The app does NOT register devices.** Device pairing is a hardware process. To test the app, seed device and permission rows:

```sql
-- 1. Create artefacto
INSERT INTO artefactos (mac, nombre_personalizado, nivel_prioridad, limite_consumo_w, is_online, is_encendido)
VALUES ('00:1B:44:11:3A:B7', 'Router Principal', 'media', 150.00, false, false)
ON DUPLICATE KEY UPDATE mac = mac;

-- 2. Get IDs
SELECT id FROM artefactos WHERE mac = '00:1B:44:11:3A:B7';
SELECT id FROM usuarios WHERE auth0_id = '<AUTH0_SUB>';

-- 3. Link user to device
INSERT INTO permisos_usuario_artefacto (id_usuario, id_artefacto, nivel_acceso)
VALUES (<user_id>, <artefacto_id>, 'ADMIN')
ON DUPLICATE KEY UPDATE nivel_acceso = 'ADMIN';
```

**Or use the seed script:**
```bash
python scripts/seed_test_device.py --mac 00:1B:44:11:3A:B7 --auth0-id <AUTH0_SUB>
```

---

### Step 4: List Devices (App)
```bash
GET /api/dispositivos
Authorization: Bearer <REAL_AUTH0_ACCESS_TOKEN>
```
**Expected:** `200 OK`, array with the seeded device from Step 3.

```json
[
  {
    "id": 1,
    "mac": "00:1B:44:11:3A:B7",
    "nombre_personalizado": "Router Principal",
    "nivel_prioridad": "media",
    "limite_consumo_w": 150.00,
    "is_online": false,
    "is_encendido": false,
    "nivel_acceso": "ADMIN",
    "last_seen_at": null
  }
]
```

**If still empty:** Step 3 failed — check user ID and artefacto ID match.

---

### Step 5: Get Device Detail (App)
```bash
GET /api/dispositivos/00:1B:44:11:3A:B7
Authorization: Bearer <REAL_AUTH0_ACCESS_TOKEN>
```
**Expected:** `200 OK`, same shape as single device object.

**If 403:** No `permisos_usuario_artefacto` row for this user + device.
**If 404:** Device MAC not found in `artefactos` table.

---

### Step 6: Toggle Relay (App)
```bash
POST /api/dispositivos/00:1B:44:11:3A:B7/comando/estado
Authorization: Bearer <REAL_AUTH0_ACCESS_TOKEN>

{ "encendido": true }
```
**Expected:** `200 OK`, empty JSON `{}`

**Side effect:** MQTT message published to `smartups/dispositivos/00:1B:44:11:3A:B7/comando/estado`

---

### Step 7: Update Limits (App)
```bash
POST /api/dispositivos/00:1B:44:11:3A:B7/comando/limites
Authorization: Bearer <REAL_AUTH0_ACCESS_TOKEN>

{ "limite_voltaje": 14.0, "limite_corriente": 10.0, "limite_potencia": 200.0 }
```
**Expected:** `200 OK`, empty JSON `{}`

**Validation bounds:**
| Field | Min | Max |
|---|---|---|
| `limite_voltaje` | 0.1 | 60.0 |
| `limite_corriente` | 0.1 | 30.0 |
| `limite_potencia` | 0.1 | 500.0 |

---

### Step 8: Telemetry (App)
```bash
GET /api/dispositivos/00:1B:44:11:3A:B7/telemetria?limite=50
Authorization: Bearer <REAL_AUTH0_ACCESS_TOKEN>
```
**Expected:** `200 OK`, `[]` initially (no data yet).

**To generate test telemetry:**
```bash
cd /home/manu0ak/iot_backend && python -m app.mock_esp32
```
Then re-run Step 8 — should return non-empty array.

**Telemetry response shape:**
```json
[
  {
    "id": 1,
    "mac_dispositivo": "00:1B:44:11:3A:B7",
    "timestamp": "2026-05-12T14:30:00Z",
    "voltaje": 120.50,
    "corriente": 2.30,
    "potencia": 277.15,
    "tiempo_operacion_s": 3600,
    "estado_sin_cambios": false
  }
]
```

**Note:** All datetime fields now include `Z` suffix (UTC timezone).

---

### Step 9: WebSocket (Manual Test)

**Local dev:**
```javascript
const ws = new WebSocket('ws://localhost:8000/ws/telemetry?token=<ACCESS_TOKEN>');
```

**Production (MUST use TLS):**
```javascript
const ws = new WebSocket('wss://api.thesisbroker.com/ws/telemetry?token=<ACCESS_TOKEN>');
```

**Expected:** Connection opens successfully.
**Send test message:** `ws.send('ping')` → receives `Echo: ping`
**If 4001 close code:** Invalid/missing token.

---

### Backend-Only Endpoints (App Does NOT Call These)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/dispositivos` | Register device (gateway/provisioning only) |
| `PATCH` | `/api/dispositivos/{mac}` | Update device metadata (admin only) |

These endpoints exist but **the app never calls them**. Device pairing is a hardware process.

---

## 4. Endpoint Reference

### Public (No Auth)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | None | Liveness probe |

### Auth0 Webhook (Shared Secret)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/api/users/sync` | `Authorization: Bearer <BACKEND_SYNC_SECRET>` | Create/update user in DB |

### M2M (No JWT — ESP32 Gateway)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/api/telemetria` | None | Ingest telemetry from devices |

### Authenticated (JWT Bearer) — App Calls These

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/dispositivos` | List user's devices |
| `GET` | `/api/dispositivos/{mac}` | Get device detail |
| `GET` | `/api/dispositivos/{mac}/telemetria` | Get telemetry history |
| `POST` | `/api/dispositivos/{mac}/comando/estado` | Toggle relay |
| `POST` | `/api/dispositivos/{mac}/comando/limites` | Update safety limits |
| `WS` | `/ws/telemetry?token=<jwt>` | Real-time telemetry stream |

### Authenticated — Backend/Admin Only (App Does NOT Call)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/dispositivos` | Register new device |
| `PATCH` | `/api/dispositivos/{mac}` | Update device metadata |

### Legacy (Still Active — To Be Removed)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/telemetria/{mac}` | Use `/api/dispositivos/{mac}/telemetria` |
| `POST` | `/api/comando/estado` | Use `/api/dispositivos/{mac}/comando/estado` |
| `POST` | `/api/comando/limites` | Use `/api/dispositivos/{mac}/comando/limites` |
| `GET` | `/api/dispositivos/{mac}/estado` | Use `/api/dispositivos/{mac}` |

---

## 5. Error Contract

All errors return structured JSON (never FastAPI default `{detail}`):

```json
{
  "error": "<code>",
  "message": "<human readable>",
  "...": "additional context"
}
```

| `error` | HTTP | When | Example Context |
|---|---|---|---|
| `unauthorized` | 401 | Missing/invalid JWT | `{"auth0_id": "..."}` |
| `sync_unauthorized` | 401 | Wrong sync secret | None |
| `forbidden` | 403 | No device access or user not in DB | `{"mac": "..."}` or `{"auth0_id": "..."}` |
| `not_found` | 404 | Device not found | `{"mac": "..."}` |
| `validation_error` | 422 | Bad input | `{"field": "mac"}` |

**Validation error example:**
```json
{
  "error": "validation_error",
  "message": "value is not a valid email address: The email address is not valid. It must have exactly one @-sign.",
  "field": "email"
}
```

**Important:** The frontend does NOT parse FastAPI default `{"detail": "..."}`. All errors must use the `{error, message}` format above.

---

## 6. WebSocket

### Connection
```
Local dev:  ws://localhost:8000/ws/telemetry?token=<access_token>
Production: wss://api.thesisbroker.com/ws/telemetry?token=<access_token>
```

- **JWT as query param** (not header — WebSocket handshake cannot carry custom headers)
- **Close code 4001** = authentication failure
- **Echo mode** currently (real-time streaming planned)
- **Production MUST use `wss://`** — never send JWT tokens over unencrypted `ws://`

### Test Script (Node.js)
```javascript
const WebSocket = require('ws');
const token = process.argv[2];
// Local dev:
const ws = new WebSocket(`ws://localhost:8000/ws/telemetry?token=${token}`);
// Production:
// const ws = new WebSocket(`wss://api.thesisbroker.com/ws/telemetry?token=${token}`);

ws.on('open', () => {
  console.log('Connected');
  ws.send('test-message');
});

ws.on('message', (data) => {
  console.log('Received:', data.toString());
  ws.close();
});

ws.on('close', (code, reason) => {
  console.log('Closed:', code, reason.toString());
});

ws.on('error', (err) => {
  console.error('Error:', err.message);
});
```

---

## 7. Common Failures & Fixes

### "User not found or inactive" (403)
**Cause:** User exists in Auth0 but not in backend DB. Auth0 sync webhook not yet fired.
**Fix:** Ensure Auth0 Post-Login Action calls `POST /api/users/sync` after login. Check `usuarios` table for the user.

### "Unable to find signing key" (401)
**Cause:** Auth0 rotated keys, JWKS cache stale.
**Fix:** Restart backend to clear cache, or wait up to 1 hour for TTL expiry.

### "Dispositivo no autorizado" (403)
**Cause:** User has no `permisos_usuario_artefacto` row for this MAC.
**Fix:** Seed the device + permission rows (see Step 3). Device pairing is hardware-only — the app cannot register devices.

### Empty device list
**Cause:** No `permisos_usuario_artefacto` rows for this user.
**Fix:** Seed device + permission (see Step 3). Frontend falls back to hardcoded `DEVICE_REGISTRY` when empty.

### Empty telemetry array
**Cause:** Device has not published any data.
**Fix:** Run `python -m app.mock_esp32` to simulate ESP32 publishing.

### MQTT command not reaching device
**Cause:** Mosquitto not running, or device not subscribed.
**Fix:** `sudo systemctl status mosquitto` and check device MQTT connection.

### MAC format rejected (422)
**Cause:** MAC not in `AA:BB:CC:DD:EE:FF` format.
**Fix:** Normalize MAC to uppercase with colon separators.

### CORS errors in Expo web preview
**Cause:** Backend CORS not configured for Expo dev server.
**Fix:** Backend allows `http://localhost:19006`, `http://localhost:8081`, `exp://127.0.0.1:8081`, `smartsaver://callback`.

---

## Quick Verification Checklist

- [ ] `GET /health` returns 200
- [ ] `POST /api/users/sync` with correct secret returns 200
- [ ] `GET /api/dispositivos` with valid Auth0 token returns 200 (empty `[]` for new user)
- [ ] Seed device + permission rows in DB (app cannot register devices)
- [ ] Device appears in `GET /api/dispositivos` list after seeding
- [ ] `GET /api/dispositivos/{mac}` returns device detail with all fields
- [ ] `POST /api/dispositivos/{mac}/comando/estado` toggles relay
- [ ] `POST /api/dispositivos/{mac}/comando/limites` sets limits
- [ ] `GET /api/dispositivos/{mac}/telemetria` returns data after mock_esp32 runs
- [ ] WebSocket connects with `?token=` query param (local: `ws://`, prod: `wss://`)
- [ ] All errors return `{error, message, ...}` format (never `{detail}`)
- [ ] Datetime fields include `Z` suffix (UTC timezone)
- [ ] Auth0 user not found returns 403 (not 401)

---

*Last updated: 2026-05-14*
*See also: INTEGRATION_VERIFY.md — frontend contract, CORRECTIONS.md — device pairing scope*