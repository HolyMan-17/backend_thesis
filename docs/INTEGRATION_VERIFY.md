# Frontend-Backend Integration Verification Spec

*This document is a two-sided contract. The frontend has been implemented per the decisions in PLAN.md. The backend agent should verify their implementation against every row in this document. If anything does not match, flag it immediately before integration testing.*

---

## 1. Environment Variables (Both Sides)

### Frontend (`smartsaver/.env`)

```env
EXPO_PUBLIC_API_URL=https://api.thesisbroker.com
EXPO_PUBLIC_AUTH0_DOMAIN=thesisbroker.us.auth0.com
EXPO_PUBLIC_AUTH0_CLIENT_ID=iCnC8XXZHeaCNdsEULmtIYD5YL01QdDU
EXPO_PUBLIC_AUTH0_AUDIENCE=https://api.thesisbroker.com
```

### Backend (`.env`)

```env
AUTH0_DOMAIN=thesisbroker.us.auth0.com
AUTH0_AUDIENCE=https://api.thesisbroker.com
AUTH0_ISSUER=https://thesisbroker.us.auth0.com/
AUTH0_JWKS_URI=https://thesisbroker.us.auth0.com/.well-known/jwks.json
BACKEND_SYNC_SECRET=<shared-secret-from-auth0-action>
```

**Verification**: If the backend `AUTH0_DOMAIN`, `AUTH0_AUDIENCE`, or `BACKEND_SYNC_SECRET` do not match these exact values, Auth0 token validation will fail.

---

## 2. Auth0 Configuration (Already Set Up)

| Setting | Value | Verified |
|---------|-------|----------|
| Tenant | `thesisbroker.us.auth0.com` | ✅ |
| Client ID | `iCnC8XXZHeaCNdsEULmtIYD5YL01QdDU` | ✅ |
| Audience | `https://api.thesisbroker.com` | ✅ |
| App Type | Native | ✅ |
| Grant Types | Authorization Code + Refresh Token | ✅ |
| Token Expiration | 900s (15 min) | ✅ |
| Refresh Token Rotation | Auto-enabled (single-use) | ✅ |
| Callback URLs | `smartsaver://callback`, `exp://127.0.0.1:8081` | ✅ |
| Logout URL | `smartsaver://callback` | ✅ |
| Scopes requested by app | `openid profile email offline_access read:devices write:devices read:logs` | ✅ |

**Backend note**: The app requests `read:devices write:devices read:logs`. For now (single-user), all scopes are granted to every authenticated user. The backend does not need to enforce scope-based authorization yet, but the `scope` claim must be present in the JWT.

---

## 3. API Contract Matrix

The frontend calls **exactly these endpoints** in **exactly this way**. Any deviation on the backend will cause the frontend to fail.

### 3.1 Health Check (Unauthenticated)

```
GET {API_BASE_URL}/health
```

- **No `Authorization` header**
- **Expected 200**: any JSON body (frontend just checks `res.ok`)

### 3.2 List User's Devices

```
GET {API_BASE_URL}/api/dispositivos
Authorization: Bearer <access_token>
```

**Expected 200 Response** (array, must not be wrapped in an object):
```json
[
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
]
```

**Frontend behavior**:
- If response is empty array `[]` or HTTP error, frontend falls back to hardcoded `DEVICE_REGISTRY` (3 devices)
- The frontend polls this every 5 seconds on `DevicesScreen` and `HomeScreen`

### 3.3 Device Detail

```
GET {API_BASE_URL}/api/dispositivos/{mac}
Authorization: Bearer <access_token>
```

**Expected 200 Response**:
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

**Expected 404 Response** (structured JSON, NOT FastAPI default `{"detail": "..."}`):
```json
{
  "error": "not_found",
  "message": "Dispositivo no encontrado",
  "mac": "00:1B:44:11:3A:B7"
}
```

### 3.4 Telemetry History

```
GET {API_BASE_URL}/api/dispositivos/{mac}/telemetria?limite=50
Authorization: Bearer <access_token>
```

**Expected 200 Response** (array, DESC order — newest first):
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

**Critical**: The frontend expects `mac_dispositivo` (not `mac`) in the telemetry response. The `timestamp` field must be ISO 8601. The array must be in DESC order (newest first) because `AnalyticsScreen` uses `history[0]` as the latest reading.

### 3.5 Toggle Device State (Relay)

```
POST {API_BASE_URL}/api/dispositivos/{mac}/comando/estado
Authorization: Bearer <access_token>
Content-Type: application/json

{"encendido": true}
```

**Request body**: ONLY `{"encendido": <boolean>}`. No `mac_dispositivo` field.

**Expected 200/204**: Frontend checks `res.ok`. No response body parsing.

### 3.6 Set Safety Limits

```
POST {API_BASE_URL}/api/dispositivos/{mac}/comando/limites
Authorization: Bearer <access_token>
Content-Type: application/json

{"limite_voltaje": 14.0, "limite_corriente": 2.0}
```

**Request body**: Only these fields, all optional:
```json
{
  "limite_voltaje": number | null,
  "limite_corriente": number | null,
  "limite_potencia": number | null
}
```

**No `mac_dispositivo` in body** — MAC is in URL path.

**Frontend pre-validation** (client-side, before sending):
- `limite_voltaje`: if present, must be ≥ 0.1 and ≤ 60
- `limite_corriente`: if present, must be ≥ 0.1 and ≤ 30
- `limite_potencia`: if present, must be ≥ 0.1 and ≤ 500
- Reject `NaN`, `Infinity`, negative values

**Backend must validate with same or stricter bounds** and return:
```json
{"error": "validation_error", "message": "..."}
```
with HTTP 422.

---

## 4. Error Response Format (Strict)

Every error response from the backend must use this exact shape. The frontend does NOT parse FastAPI default `{"detail": "..."}`.

```json
{
  "error": "<code>",
  "message": "Human-readable Spanish message",
  "mac": "00:1B:44:11:3A:B7"   // optional, include when relevant
}
```

| `error` code | HTTP | Frontend Behavior |
|---|---|---|
| `unauthorized` | 401 | Triggers token refresh + retry once. If refresh fails, forces logout. |
| `forbidden` | 403 | Shows alert "Dispositivo no autorizado". Does NOT retry. |
| `not_found` | 404 | Shows alert with `message`. Falls back to cached/hardcoded data. |
| `validation_error` | 422 | Shows alert with `message`. Keeps modal open for correction. |
| `rate_limited` | 429 | Shows alert "Demasiadas solicitudes. Inténtalo de nuevo." |

---

## 5. Authentication Flow Verification

### 5.1 Login Flow (PKCE)

```
1. User taps "Iniciar Sesión"
2. Frontend opens browser to:
   https://thesisbroker.us.auth0.com/authorize?
     response_type=code
     &client_id=iCnC8XXZHeaCNdsEULmtIYD5YL01QdDU
     &redirect_uri=smartsaver%3A%2F%2Fcallback
     &audience=https%3A%2F%2Fapi.thesisbroker.com
     &scope=openid%20profile%20email%20offline_access%20read%3Adevices%20write%3Adevices%20read%3Alogs
     &code_challenge=<sha256_base64url>
     &code_challenge_method=S256
     &state=<random_16_char>

3. Auth0 redirects to: smartsaver://callback?code=xxx&state=yyy
4. Frontend exchanges code + PKCE verifier for tokens via:
   POST https://thesisbroker.us.auth0.com/oauth/token
5. Tokens stored in expo-secure-store
6. Frontend extracts user profile from ID token claims
7. App renders (auth guard passes)
```

**Backend note**: The Auth0 Post-Login Action calls `POST /api/users/sync` with `BACKEND_SYNC_SECRET`. The backend must upsert the user into the `usuarios` table at this point.

### 5.2 Token Refresh

```
1. Frontend detects token expiry (checks SecureStore timestamp)
2. Calls POST https://thesisbroker.us.auth0.com/oauth/token
   with refresh_token grant
3. Auth0 returns new access_token + refresh_token (rotation)
4. Frontend stores new tokens, discards old refresh_token
```

**Backend note**: The backend never sees the refresh token. Only the access token (JWT) is sent to the backend.

### 5.3 API Request with JWT

```
Every request to /api/* includes:
Authorization: Bearer <access_token>
```

**Backend must validate**:
1. Extract `Authorization: Bearer <token>` header
2. Validate JWT signature against JWKS from `https://thesisbroker.us.auth0.com/.well-known/jwks.json`
3. `iss` must equal `https://thesisbroker.us.auth0.com/`
4. `aud` must equal `https://api.thesisbroker.com`
5. `exp` must not be in the past
6. Extract `sub` claim → lookup `usuarios` table by `auth0_id = sub`
7. If user not found → 403 Forbidden
8. Inject user into `request.state.user`

### 5.4 Logout Flow

```
1. Frontend calls Auth0 /v2/logout (browser-based)
2. Frontend calls Auth0 /oauth/revoke (best-effort token revocation)
3. Frontend clears SecureStore (access_token, refresh_token, id_token, expiry)
4. Frontend clears AsyncStorage (user preferences, logs, etc.)
5. Frontend sets isAuthenticated = false → LoginScreen shown
```

**Backend note**: Logout does NOT call any backend endpoint. The backend should rely on token expiry + JWT validation. If you maintain a session store, the app cannot invalidate it remotely.

---

## 6. WebSocket Contract (When Enabled)

**Current status**: WebSocket is **disabled** in the app. `useTelemetryStore` uses mock data + 5s HTTP polling instead.

When enabled, the frontend will connect to:
```
wss://api.thesisbroker.com/ws/telemetry?token=<access_token>
```

**Backend must**:
1. Extract `token` query parameter
2. Validate JWT (same rules as REST middleware)
3. If invalid/expired → close connection with code `4001` and reason `"Unauthorized"`
4. If valid → accept connection, filter telemetry events to only devices the user has access to via `permisos_usuario_artefacto`
5. On token expiry during active connection → close with code `4001`

**Frontend behavior on 4001**:
- Stops reconnecting
- Forces re-login (shows LoginScreen)

---

## 7. Frontend Auth Guard State Machine

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

**Critical**: The auth check happens FIRST in `_layout.tsx`. Onboarding is a SECOND gate after auth. The backend does not need to know about onboarding state.

---

## 8. Data Flow: Device Registry Migration

### Current Behavior (Frontend)

1. `DevicesScreen` mounts → calls `apiClient.getDevices()`
2. If API returns non-empty array → render those devices
3. If API fails or returns empty → fall back to `DEVICE_REGISTRY` (3 hardcoded devices)
4. Polls every 5 seconds

### Target Behavior (Requires Backend)

1. `DevicesScreen` mounts → calls `GET /api/dispositivos`
2. Backend returns user's actual devices from `permisos_usuario_artefacto` JOIN
3. Frontend renders real devices
4. Fallback to hardcoded list only on network failure

**Backend must implement**: `GET /api/dispositivos` (section 3.2) for this to work end-to-end.

---

## 9. Integration Test Checklist

Run these steps in order to verify frontend-backend compatibility.

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
- [ ] Backend returns device object with all fields (`id`, `mac`, `nombre_personalizado`, `is_online`, `is_encendido`, etc.)
- [ ] If device not found → backend returns `{"error": "not_found", "message": "...", "mac": "..."}` with 404

### Test 4: Telemetry
- [ ] Frontend calls `GET /api/dispositivos/00:1B:44:11:3A:B7/telemetria?limite=50`
- [ ] Backend returns array of telemetry objects in DESC order
- [ ] Each object has `mac_dispositivo`, `timestamp`, `voltaje`, `corriente`, `potencia`

### Test 5: Toggle Relay
- [ ] Frontend calls `POST /api/dispositivos/00:1B:44:11:3A:B7/comando/estado` with `{"encendido": true}`
- [ ] Backend validates JWT, checks device permissions, updates relay state, publishes MQTT
- [ ] Backend returns 200/204

### Test 6: Set Limits
- [ ] Frontend calls `POST /api/dispositivos/00:1B:44:11:3A:B7/comando/limites` with `{"limite_voltaje": 14.0}`
- [ ] Backend validates input (bounds: V 0.1-60, A 0.1-30, W 0.1-500)
- [ ] Backend returns 200 on success, 422 with `{"error": "validation_error", "message": "..."}` on invalid input

### Test 7: Token Expiry + Refresh
- [ ] Wait 15 minutes for access token to expire
- [ ] Frontend makes API call → backend returns 401
- [ ] Frontend automatically refreshes token via Auth0
- [ ] Frontend retries original API call with new token → succeeds

### Test 8: Logout
- [ ] User taps "Cerrar Sesión" in Settings
- [ ] Frontend clears SecureStore and AsyncStorage
- [ ] Frontend shows LoginScreen
- [ ] Subsequent API calls fail with 401 (no token)

### Test 9: Factory Reset
- [ ] User taps "Restablecer Aplicación a Valores de Fábrica"
- [ ] Frontend clears ALL local data (logs, preferences, user name)
- [ ] Frontend calls logout → tokens cleared
- [ ] App returns to LoginScreen

---

## 10. Known Frontend Issues / TODOs

| Issue | Location | Impact | Fix Needed |
|-------|----------|--------|------------|
| `require()` used in `apiClient.ts` for lazy imports | `src/services/apiClient.ts:44,50` | Lint warning only | Refactor to dynamic `import()` if strict ESM needed |
| React Hook missing dependencies | Multiple screens | Lint warnings | Non-breaking, can fix incrementally |
| `width` unused in OnboardingScreen | `OnboardingScreen.tsx:10` | Lint warning | Remove unused destructuring |
| WebSocket disabled | `useTelemetryStore` bypasses WS | No live telemetry | Enable WS when backend `wss://` is ready |

---

## 11. Contact / Questions

If the backend agent finds any mismatch between this document and their implementation, flag it immediately. Do NOT silently change the frontend contract without updating this document.

Key files on frontend:
- `src/services/apiClient.ts` — all REST calls
- `src/services/authService.ts` — Auth0 PKCE flow
- `src/services/WebSocketService.ts` — WS contract (disabled)
- `src/types/api.ts` — TypeScript request/response shapes
- `app/_layout.tsx` — auth guard

**Last updated**: 2026-05-13
**Frontend status**: Phase 2 Complete, type-check clean, lint clean

