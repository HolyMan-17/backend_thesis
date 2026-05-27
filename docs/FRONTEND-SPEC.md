# SmartSaver IoT Backend — Frontend Specification

> **Single source of truth** for frontend-backend integration.
> Contains: API contract, Auth0 flow, schema definitions, WebSocket protocol, test checklist, troubleshooting.
>
> **Last updated:** 2026-05-21
> **Backend version:** V5.0
>
> **⚠️ BREAKING CHANGE V5.0:** `is_encendido` removed from `DispositivoResponse`. Use `estado_reportado` for relay state and `estado_deseado` for pending commands. `limite_*` fields still present but now stored in normalized table.

---

## Table of Contents

1. [Environment & Configuration](#1-environment--configuration)
2. [Auth0 Authentication](#2-auth0-authentication)
3. [API Endpoints](#3-api-endpoints)
4. [Request / Response Schemas](#4-request--response-schemas)
5. [Error Contract](#5-error-contract)
6. [WebSocket Real-Time Streaming](#6-websocket-real-time-streaming)
7. [Integration Test Sequence](#7-integration-test-sequence)
8. [Frontend State Machine](#8-frontend-state-machine)
9. [Common Failures & Fixes](#9-common-failures--fixes)
10. [Quick Verification Checklist](#10-quick-verification-checklist)
11. [Known Issues](#11-known-issues)

---

## 1. Environment & Configuration

### URLs

| Component | Local Dev | Production |
|---|---|---|
| API Base | `http://localhost:8000` | `https://api.thesisbroker.com` |
| Auth0 Domain | `thesisbroker.us.auth0.com` | `thesisbroker.us.auth0.com` |
| Auth0 Audience | `https://api.thesisbroker.com` | `https://api.thesisbroker.com` |
| Mosquitto MQTT | `127.0.0.1:1883` | (AWS IoT Core or equivalent) |

### Frontend `.env`

```env
EXPO_PUBLIC_API_URL=https://api.thesisbroker.com
EXPO_PUBLIC_AUTH0_DOMAIN=thesisbroker.us.auth0.com
EXPO_PUBLIC_AUTH0_CLIENT_ID=iCnC8XXZHeaCNdsEULmtIYD5YL01QdDU
EXPO_PUBLIC_AUTH0_AUDIENCE=https://api.thesisbroker.com
```

### Backend `.env`

```env
AUTH0_DOMAIN=thesisbroker.us.auth0.com
AUTH0_AUDIENCE=https://api.thesisbroker.com
AUTH0_ISSUER=https://thesisbroker.us.auth0.com/
AUTH0_JWKS_URI=https://thesisbroker.us.auth0.com/.well-known/jwks.json
BACKEND_SYNC_SECRET=<shared-secret>
```

### CORS Origins (backend allows)

- `http://localhost:19006` (Expo web dev)
- `http://localhost:3000` (web dev)
- `http://localhost:8081` (Expo dev client)
- `exp://127.0.0.1:8081` (Expo Go)
- `smartsaver://callback` (deep link)

### Headers for all authenticated requests

```
Authorization: Bearer <access_token>
Content-Type: application/json
```

---

## 2. Auth0 Authentication

### Auth0 Configuration

| Setting | Value |
|---|---|
| Tenant | `thesisbroker.us.auth0.com` |
| Client ID | `iCnC8XXZHeaCNdsEULmtIYD5YL01QdDU` |
| Audience | `https://api.thesisbroker.com` |
| App Type | Native |
| Grant Types | Authorization Code + Refresh Token |
| Token Expiration | 900s (15 min) |
| Refresh Token Rotation | Auto-enabled (single-use) |
| Callback URLs | `smartsaver://callback`, `exp://127.0.0.1:8081` |
| Logout URL | `smartsaver://callback` |
| Scopes requested | `openid profile email offline_access read:devices write:devices read:logs` |

### PKCE Login Flow

```
1. User taps "Iniciar Sesión"
2. Frontend opens browser:
   https://thesisbroker.us.auth0.com/authorize?
     response_type=code
     &client_id=iCnC8XXZHeaCNdsEULmtIYD5YL01QdDU
     &redirect_uri=smartsaver%3A%2F%2Fcallback
     &audience=https%3A%2F%2Fapi.thesisbroker.com
     &scope=openid%20profile%20email%20offline_access%20read%3Adevices%20write%3Adevices%20read%3Alogs
     &code_challenge=<sha256_base64url>
     &code_challenge_method=S256
     &state=<random_16_char>

3. Auth0 redirects: smartsaver://callback?code=xxx&state=yyy
4. Frontend exchanges code + PKCE verifier:
   POST https://thesisbroker.us.auth0.com/oauth/token
5. Tokens stored in expo-secure-store
6. App renders (auth guard passes)
```

### User Sync Webhook

**Critical:** The backend rejects ALL authenticated calls (`403 Forbidden`) until the user exists in the `usuarios` table.

Auth0 Post-Login Action calls:

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

**Response:**
```json
{ "status": "synced", "auth0_id": "auth0|64f8a1b2c3d4e5f6a7b8c9d0" }
```

### Token Refresh

```
1. Frontend detects token expiry
2. Calls POST https://thesisbroker.us.auth0.com/oauth/token
   with refresh_token grant
3. Auth0 returns new access_token + refresh_token (rotation)
4. Frontend stores new tokens, discards old refresh_token
```

**Backend note:** The backend never sees the refresh token. Only the access token (JWT) is sent to the backend.

### Token Requirements

- **Algorithm:** RS256
- **Audience:** `https://api.thesisbroker.com`
- **Issuer:** `https://thesisbroker.us.auth0.com/`
- **Required claim:** `sub` (Auth0 user ID)
- **JWKS URI:** `https://thesisbroker.us.auth0.com/.well-known/jwks.json`
- Backend caches JWKS for 1 hour

### User Not Found → 403

```json
{ "error": "forbidden", "message": "User not found or inactive", "auth0_id": "auth0|..." }
```

Frontend should NOT retry — force logout and prompt re-login.

### Logout Flow

```
1. Frontend calls Auth0 /v2/logout (browser-based)
2. Frontend calls Auth0 /oauth/revoke (best-effort)
3. Frontend clears SecureStore and AsyncStorage
4. Frontend sets isAuthenticated = false → LoginScreen
```

**Backend note:** Logout does NOT call any backend endpoint. Backend relies on token expiry + JWT validation.

---

## 3. API Endpoints

### Public (No Auth)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness probe |

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
| `PATCH` | `/api/dispositivos/{mac}` | Update device metadata |
| `DELETE` | `/api/dispositivos/{mac}` | Soft delete device |
| `GET` | `/api/dispositivos/{mac}/telemetria?limite=50` | Get telemetry history |
| `GET` | `/api/dispositivos/{mac}/agregados?granularity=hour|day` | Telemetry aggregates |
| `POST` | `/api/dispositivos/{mac}/comando/estado` | Toggle relay (activates 5min lease) |
| `POST` | `/api/dispositivos/{mac}/comando/limites` | Update safety limits |
| `GET` | `/api/alertas?solo_activas=true|false` | List alerts |
| `PATCH` | `/api/alertas/{alerta_id}` | Resolve alert |
| `GET` | `/api/eventos?mac=...&limite=50` | List events |
| `WS` | `/ws/telemetry?token=<jwt>` | Real-time telemetry stream |

### Backend/Admin Only (App Does NOT Call)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/dispositivos` | Register new device |

### Legacy (Still Active — To Be Removed)

| Method | Path | Use Instead |
|---|---|---|
| `GET` | `/api/telemetria/{mac_dispositivo}` | `/api/dispositivos/{mac}/telemetria` |
| `POST` | `/api/comando/estado` | `/api/dispositivos/{mac}/comando/estado` |
| `POST` | `/api/comando/limites` | `/api/dispositivos/{mac}/comando/limites` |
| `GET` | `/api/dispositivos/{mac_dispositivo}/estado` | `/api/dispositivos/{mac}` |

---

## 4. Request / Response Schemas

### Device List / Detail (`GET /api/dispositivos`, `GET /api/dispositivos/{mac}`)

**Response (array for list, object for detail):**

```json
{
  "id": 1,
  "mac": "00:1B:44:11:3A:B7",
  "nombre_personalizado": "Kitchen Light",
  "nivel_prioridad": "alta",
  "limite_consumo_w": 150.00,
  "limite_voltaje": 14.00,
  "limite_corriente": 2.00,
  "limite_potencia": 200.00,
  "estado_deseado": true,
  "estado_reportado": false,
  "is_online": true,
  "nivel_acceso": "ADMIN",
  "last_seen_at": "2026-05-12T14:30:00Z"
}
```

**Frontend notes:**
- `nombre_personalizado` is `null` by default — fall back to MAC address
- `limite_voltaje`, `limite_corriente`, `limite_potencia` are nullable
- `estado_deseado` vs `estado_reportado` = device shadow. When they differ, show "syncing" spinner
- Frontend polls `GET /api/dispositivos` every 5 seconds on DevicesScreen and HomeScreen
- If API returns empty `[]` or error, frontend falls back to hardcoded `DEVICE_REGISTRY`

### Update Device (`PATCH /api/dispositivos/{mac}`)

**Request (partial, send only changed fields):**

```json
{
  "nombre_personalizado": "Kitchen Light",
  "nivel_prioridad": "alta",
  "limite_consumo_w": 150.0,
  "limite_voltaje": 14.0,
  "limite_corriente": 2.0,
  "limite_potencia": 200.0
}
```

**`nombre_personalizado` validation:**
- Empty strings (`""`) and whitespace-only strings (`"   "`) → `422`
- Leading/trailing whitespace is stripped before storage
- Send `null` to clear the name
- Duplicate names across devices are allowed

**Response:** `200 OK` with full `DispositivoResponse`

### Toggle Relay (`POST /api/dispositivos/{mac}/comando/estado`)

**Request:**
```json
{ "encendido": true }
```

**Response:** `200 OK`, empty `{}`

**Side effects:**
- MQTT published to `smartups/dispositivos/{mac}/comando/estado`
- Backend activates a 5-minute user lease. During this time, AI/auto-scheduler cannot override.
- Safety alerts (overvoltage / overcurrent) immediately break the lease.

### Update Limits (`POST /api/dispositivos/{mac}/comando/limites`)

**Request:**
```json
{ "limite_consumo_w": 150.0, "limite_voltaje": 14.0, "limite_corriente": 10.0, "limite_potencia": 200.0 }
```

**Validation bounds:**

| Field | Min | Max |
|---|---|---|
| `limite_consumo_w` | 0 | — |
| `limite_voltaje` | 0.1 | 60.0 |
| `limite_corriente` | 0.1 | 30.0 |
| `limite_potencia` | 0.1 | 500.0 |

**Important:** Limits are persisted to DB before MQTT publish. Frontend should re-fetch device detail to verify persistence.

**Response:** `200 OK`, empty `{}`

### Telemetry History (`GET /api/dispositivos/{mac}/telemetria?limite=50`)

**Response (array, DESC order — newest first):**

```json
[
  {
    "id": 1,
    "mac_dispositivo": "00:1B:44:11:3A:B7",
    "timestamp": "2026-05-12T14:30:00Z",
    "voltaje": 12.10,
    "corriente": 1.80,
    "potencia": 21.78,
    "tiempo_operacion_s": 1715682000,
    "estado_sin_cambios": false
  }
]
```

**Critical:**
- `mac_dispositivo` (not `mac`) in telemetry response
- `timestamp` is ISO 8601 with `Z` suffix (UTC)
- Array is DESC order (newest first) — `AnalyticsScreen` uses `history[0]` as latest

### Telemetry Aggregates (`GET /api/dispositivos/{mac}/agregados`)

**Parameters:**
- `granularity`: `hour` | `day` (default: `hour`)
- `desde`: ISO 8601 datetime (default: 24h ago)
- `hasta`: ISO 8601 datetime (default: now)

**Response:**

```json
[
  {
    "bucket": "2026-05-20T14:00:00Z",
    "potencia_promedio_w": 145.50,
    "potencia_maxima_w": 200.10,
    "energia_wh": 145.50
  }
]
```

**Frontend note:** Used in `AnalyticsScreen` for dashboard charts.

### Alerts (`GET /api/alertas?solo_activas=true`)

**Response:**

```json
[
  {
    "id": 1,
    "id_artefacto": 1,
    "tipo_alerta": "sobrepotencia",
    "mensaje": "Potencia 250.00W excede límite 200.00W",
    "severidad": "alta",
    "leido": false,
    "resuelto": false,
    "timestamp": "2026-05-12T14:30:00Z"
  }
]
```

**Resolve alert (`PATCH /api/alertas/{alerta_id}`):**
```json
{ "resuelto": true }
```

**Frontend note:** Show active alerts as badges/notifications. Allow user to dismiss.

### Events (`GET /api/eventos?mac=...&limite=50`)

**Response:**

```json
[
  {
    "id": 1,
    "id_artefacto": 1,
    "id_usuario": 1,
    "accion": "comando_estado",
    "razon_disparo": "Relay encendido por usuario (lease activado)",
    "timestamp": "2026-05-12T14:30:00Z"
  }
]
```

**Frontend note:** Show in activity log / audit trail. `safety_override` events indicate the AI lease was broken by a safety alert.

### Delete Device (`DELETE /api/dispositivos/{mac}`)

**Response:**
```json
{ "status": "deleted", "mac": "00:1B:44:11:3A:B7" }
```

**Note:** Soft delete. Telemetry for deleted devices is rejected. Re-registering restores the device.

---

## 5. Error Contract

All errors return structured JSON. The frontend does NOT parse FastAPI default `{"detail": "..."}`.

```json
{
  "error": "<code>",
  "message": "Human-readable Spanish message",
  "mac": "00:1B:44:11:3A:B7"
}
```

| `error` code | HTTP | When | Frontend Behavior |
|---|---|---|---|
| `unauthorized` | 401 | Missing/invalid JWT | Trigger token refresh + retry once. If refresh fails, force logout. |
| `sync_unauthorized` | 401 | Wrong sync secret | N/A (backend-only) |
| `forbidden` | 403 | No device access or user not in DB | Show alert. Do NOT retry. |
| `not_found` | 404 | Device not found | Show alert. Fall back to cached/hardcoded data. |
| `validation_error` | 422 | Bad input | Show alert. Keep modal open for correction. |
| `rate_limited` | 429 | Too many requests | Show alert "Demasiadas solicitudes. Inténtalo de nuevo." |

**Validation error example:**
```json
{
  "error": "validation_error",
  "message": "El nombre no puede estar vacío",
  "field": "nombre_personalizado"
}
```

---

## 6. WebSocket Real-Time Streaming

### Connection

```javascript
// Local dev
const ws = new WebSocket('ws://localhost:8000/ws/telemetry?token=<ACCESS_TOKEN>');

// Production (MUST use TLS)
const ws = new WebSocket('wss://api.thesisbroker.com/ws/telemetry?token=<ACCESS_TOKEN>');
```

- JWT as **query param** (not header — WebSocket handshake cannot carry custom headers)
- **Close code 4001** = authentication failure → frontend stops reconnecting, forces re-login
- Events filtered to user's devices only
- Production MUST use `wss://`

### Message Handling

```javascript
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === 'telemetria') {
    updateTelemetry(msg.mac, msg.data);
  } else if (msg.type === 'conexion') {
    updateOnlineStatus(msg.mac, msg.data.is_online);
  }
};
```

### Message Formats

**Telemetry:**
```json
{
  "type": "telemetria",
  "mac": "00:1B:44:11:3A:B7",
  "data": {
    "voltaje": 12.5,
    "corriente": 1.8,
    "potencia": 22.5,
    "tiempo_operacion_s": 3600
  }
}
```

**Connection event:**
```json
{
  "type": "conexion",
  "mac": "00:1B:44:11:3A:B7",
  "data": { "is_online": true }
}
```

**Frontend note:** No need to send messages to the server — server pushes only.

---

## 7. Integration Test Sequence

Run these in order. Each step depends on the previous one succeeding.

### Test 1: Auth0 Login + User Sync
- [ ] User taps "Iniciar Sesión" → Auth0 login page opens
- [ ] User authenticates → redirect to `smartsaver://callback?code=...`
- [ ] Frontend exchanges code for tokens (no error)
- [ ] Auth0 Post-Login Action calls `POST /api/users/sync` → backend returns `{"status": "synced"}`
- [ ] User appears in `usuarios` table with correct `auth0_id`, `email`, `nombre`

### Test 2: Authenticated API Call
- [ ] Frontend calls `GET /api/dispositivos` with `Authorization: Bearer <token>`
- [ ] Backend validates JWT successfully
- [ ] Backend looks up user by `sub` claim → finds row in `usuarios`
- [ ] Backend returns user's devices (or empty array if no permissions yet)

### Test 3: Device Detail
- [ ] Frontend calls `GET /api/dispositivos/00:1B:44:11:3A:B7`
- [ ] Backend returns device object with all fields (`id`, `mac`, `nombre_personalizado`, `limite_*`, `is_online`, `estado_deseado`, `estado_reportado`, etc.)
- [ ] If device not found → backend returns `{"error": "not_found", "message": "...", "mac": "..."}` with 404

### Test 4: Telemetry
- [ ] Frontend calls `GET /api/dispositivos/00:1B:44:11:3A:B7/telemetria?limite=50`
- [ ] Backend returns array of telemetry objects in DESC order
- [ ] Each object has `mac_dispositivo`, `timestamp`, `voltaje`, `corriente`, `potencia`

### Test 5: Telemetry Aggregates
- [ ] Frontend calls `GET /api/dispositivos/00:1B:44:11:3A:B7/agregados?granularity=hour`
- [ ] Backend returns array of time buckets with `potencia_promedio_w`, `potencia_maxima_w`, `energia_wh`

### Test 6: Toggle Relay
- [ ] Frontend calls `POST /api/dispositivos/00:1B:44:11:3A:B7/comando/estado` with `{"encendido": true}`
- [ ] Backend validates JWT, checks device permissions, sets `estado_deseado`, activates 5min lease, publishes MQTT
- [ ] Backend returns 200/204

### Test 7: Set Limits
- [ ] Frontend calls `POST /api/dispositivos/00:1B:44:11:3A:B7/comando/limites` with `{"limite_voltaje": 14.0}`
- [ ] Backend validates input (bounds: V 0.1-60, A 0.1-30, W 0.1-500)
- [ ] Backend persists limits to DB, publishes MQTT
- [ ] Backend returns 200 on success, 422 with `{"error": "validation_error", "message": "..."}` on invalid input

### Test 8: Alerts
- [ ] Frontend calls `GET /api/alertas?solo_activas=true`
- [ ] Backend returns alerts for user's devices
- [ ] Frontend calls `PATCH /api/alertas/{alerta_id}` with `{"resuelto": true}`
- [ ] Backend marks alert as resolved

### Test 9: Events
- [ ] Frontend calls `GET /api/eventos?mac=00:1B:44:11:3A:B7&limite=50`
- [ ] Backend returns event log for the device
- [ ] `safety_override` events appear when voltage/current alerts break a user lease

### Test 10: Token Expiry + Refresh
- [ ] Wait 15 minutes for access token to expire
- [ ] Frontend makes API call → backend returns 401
- [ ] Frontend automatically refreshes token via Auth0
- [ ] Frontend retries original API call with new token → succeeds

### Test 11: Logout
- [ ] User taps "Cerrar Sesión" in Settings
- [ ] Frontend clears SecureStore and AsyncStorage
- [ ] Frontend shows LoginScreen
- [ ] Subsequent API calls fail with 401 (no token)

### Test 12: WebSocket
- [ ] Frontend connects to `wss://api.thesisbroker.com/ws/telemetry?token=<token>`
- [ ] Backend validates token, accepts connection
- [ ] When device publishes telemetry, frontend receives real-time JSON message
- [ ] When device goes offline, frontend receives `conexion` event

---

## 8. Frontend State Machine

### Auth Guard

```
App Launch
  │
  ├─ isLoading = true → Show ActivityIndicator (splash)
  │
  ├─ rehydrate():
  │    ├─ No tokens in SecureStore → isAuthenticated = false → LoginScreen
  │    ├─ Token expired → refreshAccessToken()
  │    │    ├─ Refresh success → isAuthenticated = true
  │    │    └─ Refresh fails → isAuthenticated = false → LoginScreen
  │    └─ Token valid → isAuthenticated = true
  │
  └─ After auth:
       ├─ onboarding not done → OnboardingScreen (pre-filled with authUser.name)
       └─ onboarding done → HomeScreen
```

**Critical:** Auth check happens FIRST in `_layout.tsx`. Onboarding is a SECOND gate after auth. The backend does not need to know about onboarding state.

### Device Data Flow

1. `DevicesScreen` mounts → calls `apiClient.getDevices()` (`GET /api/dispositivos`)
2. If API returns non-empty array → render those devices
3. If API fails or returns empty → fall back to `DEVICE_REGISTRY` (3 hardcoded devices)
4. Polls every 5 seconds

---

## 9. Common Failures & Fixes

### "User not found or inactive" (403)
**Cause:** User exists in Auth0 but not in backend DB.
**Fix:** Ensure Auth0 Post-Login Action calls `POST /api/users/sync` after login.

### "Unable to find signing key" (401)
**Cause:** Auth0 rotated keys, JWKS cache stale.
**Fix:** Restart backend to clear cache, or wait up to 1 hour for TTL expiry.

### "Dispositivo no autorizado" (403)
**Cause:** User has no `permisos_usuario_artefacto` row for this MAC.
**Fix:** Seed the device + permission rows. Device pairing is hardware-only.

### Empty device list
**Cause:** No `permisos_usuario_artefacto` rows for this user.
**Fix:** Seed device + permission.

### Empty telemetry array
**Cause:** Device has not published any data.
**Fix:** Run `python -m app.mock_esp32` to simulate ESP32 publishing.

### MQTT command not reaching device
**Cause:** Mosquitto not running, or device not subscribed.
**Fix:** `sudo systemctl status mosquitto` and check device MQTT connection.

### MAC format rejected (422)
**Cause:** MAC not in `AA:BB:CC:DD:EE:FF` format.
**Fix:** Normalize MAC to uppercase with colon separators.

---

## 10. Quick Verification Checklist

- [ ] `GET /health` returns 200
- [ ] `POST /api/users/sync` with correct secret returns 200
- [ ] `GET /api/dispositivos` with valid Auth0 token returns 200
- [ ] Seed device + permission rows in DB
- [ ] Device appears in `GET /api/dispositivos` list after seeding
- [ ] `GET /api/dispositivos/{mac}` returns device detail with all fields (including `estado_deseado`, `estado_reportado`, `limite_*`)
- [ ] `PATCH /api/dispositivos/{mac}` updates name and limits
- [ ] `POST /api/dispositivos/{mac}/comando/estado` toggles relay and activates lease
- [ ] `POST /api/dispositivos/{mac}/comando/limites` sets limits (persisted to DB)
- [ ] `GET /api/dispositivos/{mac}/telemetria` returns data after mock_esp32 runs
- [ ] `GET /api/dispositivos/{mac}/agregados` returns time-bucketed aggregates
- [ ] `GET /api/alertas` returns alerts when thresholds exceeded
- [ ] `PATCH /api/alertas/{alerta_id}` resolves alert
- [ ] `GET /api/eventos` returns user/device events (including `safety_override`)
- [ ] `DELETE /api/dispositivos/{mac}` soft deletes device
- [ ] WebSocket streams telemetry in real-time
- [ ] All errors return `{error, message, ...}` format (never `{detail}`)
- [ ] Datetime fields include `Z` suffix (UTC timezone)

---

## 11. Known Issues

| Issue | Location | Impact | Fix Needed |
|---|---|---|---|
| `require()` used in `apiClient.ts` for lazy imports | `src/services/apiClient.ts:44,50` | Lint warning only | Refactor to dynamic `import()` if strict ESM needed |
| React Hook missing dependencies | Multiple screens | Lint warnings | Non-breaking, can fix incrementally |
| `width` unused in OnboardingScreen | `OnboardingScreen.tsx:10` | Lint warning | Remove unused destructuring |
| WebSocket enabled but not widely used | `useTelemetryStore` | Limited real-time usage | Expand WS usage across screens |

---

## Contact

If the backend agent finds any mismatch between this document and their implementation, flag it immediately. Do NOT silently change the frontend contract without updating this document.

**Key frontend files:**
- `src/services/apiClient.ts` — all REST calls
- `src/services/authService.ts` — Auth0 PKCE flow
- `src/services/WebSocketService.ts` — WS contract
- `src/types/api.ts` — TypeScript request/response shapes
- `app/_layout.tsx` — auth guard

**Backend files:**
- `app/main.py` — all endpoints
- `app/schemas.py` — Pydantic schemas
- `app/crud.py` — DB operations
- `app/mqtt_listener.py` — MQTT handler
- `app/ws_manager.py` — WebSocket manager
