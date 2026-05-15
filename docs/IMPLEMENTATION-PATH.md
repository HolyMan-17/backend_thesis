# Backend Implementation Path

> Ordered by dependency. Each step must pass before the next.
> Run `uvicorn app.main:app --reload` after each step to verify no import/syntax errors.

---

## Phase 0 — Foundation

| Step | What | Files | Why first |
|---|---|---|---|
| 0.1 | Create `requirements.txt` | `requirements.txt` | Pin deps before adding new packages |
| 0.2 | Add `app/__init__.py` | `app/__init__.py` | Make package explicit |
| 0.3 | Create `app/config.py` | `app/config.py` | Centralize all env vars (DB, MQTT, Auth0, rate limits) before auth middleware needs them |
| 0.4 | Update AGENTS.md | `AGENTS.md` | Reflect new files, commands, env vars |

**Verify:** `pip install -r requirements.txt` succeeds. `uvicorn app.main:app --reload` still starts.

---

## Phase 1 — Bug Fixes (independent, low risk)

| Step | What | Files | Detail |
|---|---|---|---|
| 1.1 | Fix `conexion` handler bug | `app/crud.py`, `app/mqtt_listener.py` | Add `actualizar_online_dispositivo(db, mac, online)` in crud.py. Call it from mqtt_listener.py `conexion` handler instead of `actualizar_estado_dispositivo`. |
| 1.2 | Remove hardcoded MQTT creds | `app/main.py`, `app/mock_esp32.py` | Read `MQTT_USER`/`MQTT_PASS` from config module instead of hardcoding. |

**Verify:** `mqtt_listener.py` no longer calls `actualizar_estado_dispositivo` for `conexion` events. `main.py` and `mock_esp32.py` have zero hardcoded credentials.

---

## Phase 2 — Error Handling

| Step | What | Files | Detail |
|---|---|---|---|
| 2.1 | Structured error responses | `app/exceptions.py` | Custom exception classes + FastAPI exception handler that returns `{error, message, context}` format. |

**Verify:** Raising custom exceptions in any route returns structured JSON, not FastAPI default `{detail}`.

---

## Phase 3 — Database & Models

| Step | What | Files | Detail |
|---|---|---|---|
| 3.1 | Add DDL for new tables | `schema_iot.sql` | `CREATE TABLE usuarios`, `CREATE TABLE permisos_usuario_artefacto`, `ALTER TABLE eventos_usuario ADD id_usuario`. |
| 3.2 | Add ORM models | `app/models.py` | `Usuario`, `PermisoUsuarioArtefacto`. Add `id_usuario` to `EventoUsuario`. Add TODO comments on `AppApiKey`, `PermisoAppArtefacto`, `CredencialMtls`. |
| 3.3 | Run DDL on database | — | Apply schema changes to MariaDB. |

**Verify:** `test_db.py` still connects. ORM models load without errors.

---

## Phase 4 — Schemas

| Step | What | Files | Detail |
|---|---|---|---|
| 4.1 | Add new schemas | `app/schemas.py` | `DispositivoCreate`, `DispositivoUpdate`, `DispositivoResponse`, `ComandoEstado`, `ComandoLimites`, `UserSyncRequest`. Add validation bounds on limits (voltage 0.1–60, current 0.1–30, power 0.1–500). |
| 4.2 | Remove old schemas | `app/schemas.py` | Remove `DispositivoEstado`, `DispositivoEstadoResponse`, `DispositivoLimites`. Modify `TelemetriaResponse`: replace `id_artefacto` with `mac_dispositivo`. |

**Verify:** No import errors from removed schemas. All new schemas validate correctly.

---

## Phase 5 — Auth Middleware

| Step | What | Files | Detail |
|---|---|---|---|
| 5.1 | JWT validation | `app/auth.py` | JWKS fetch + cache from Auth0. `get_current_user` dependency that validates Bearer token, checks `iss`/`aud`/`exp`, extracts `sub`. Looks up `usuarios` table by `auth0_id`. Returns user object. |
| 5.2 | Scope checker | `app/auth.py` | `require_scope(scope)` dependency. For now, all authenticated users get all scopes. |
| 5.3 | User sync endpoint | `app/main.py` | `POST /api/users/sync` — shared-secret auth, upsert `usuarios`. |

**Verify:** `GET /api/dispositivos` returns 401 without token. `POST /api/users/sync` with wrong secret returns 401. With correct secret, upserts user row.

---

## Phase 6 — CRUD Functions

| Step | What | Files | Detail |
|---|---|---|---|
| 6.1 | Dispositivo CRUD | `app/crud.py` | `listar_dispositivos_usuario(db, user_id)`, `obtener_dispositivo(db, mac)`, `crear_dispositivo(db, mac, user_id)`, `actualizar_dispositivo(db, mac, datos)`, `verificar_acceso(db, user_id, mac)`. |
| 6.2 | Auth helpers | `app/crud.py` | `sincronizar_usuario(db, auth0_id, email, nombre)` — upsert. |
| 6.3 | Telemetry update | `app/crud.py` | Modify `obtener_telemetria_por_mac` to join on MAC and return `mac_dispositivo` instead of `id_artefacto`. |

**Verify:** Each function works in isolation with test DB.

---

## Phase 7 — Endpoints (this is the big one)

| Step | What | Files | Detail |
|---|---|---|---|
| 7.1 | Device list | `app/main.py` | `GET /api/dispositivos` — JWT auth, returns user's devices with `nivel_acceso`. |
| 7.2 | Device register | `app/main.py` | `POST /api/dispositivos` — JWT auth, MAC only, creates artefacto + permiso row. |
| 7.3 | Device detail | `app/main.py` | `GET /api/dispositivos/{mac}` — JWT auth, authz check, returns full DispositivoResponse. |
| 7.4 | Device update | `app/main.py` | `PATCH /api/dispositivos/{mac}` — JWT auth, authz check, partial update. |
| 7.5 | Command estado | `app/main.py` | `POST /api/dispositivos/{mac}/comando/estado` — JWT auth, authz check, publishes MQTT. Replaces old `POST /api/comando/estado`. |
| 7.6 | Command limites | `app/main.py` | `POST /api/dispositivos/{mac}/comando/limites` — JWT auth, authz check, publishes MQTT. Replaces old `POST /api/comando/limites`. |
| 7.7 | Telemetry read | `app/main.py` | `GET /api/dispositivos/{mac}/telemetria` — JWT auth, authz check. Replaces old `GET /api/telemetria/{mac}`. |
| 7.8 | Remove old endpoints | `app/main.py` | Delete: `GET /api/telemetria/{mac}`, `POST /api/comando/estado`, `POST /api/comando/limites`, `GET /api/dispositivos/{mac}/estado`. |
| 7.9 | Rate limiting | `app/main.py` | Add `slowapi` middleware with per-route limits. |

**Verify:** All new endpoints respond correctly. Old endpoints return 404. Auth middleware blocks unauthenticated requests. Rate limiting triggers after threshold.

---

## Phase 8 — MQTT Updates

| Step | What | Files | Detail |
|---|---|---|---|
| 8.1 | Report topic subscriptions | `app/mqtt_listener.py` | Subscribe to `smartups/dispositivos/{mac}/reporte/estado` and `.../reporte/limites`. Handler updates `is_encendido` from `reporte/estado`. |

**Verify:** Listener subscribes to all 4 topics. `reporte/estado` updates `is_encendido`. `conexion` updates `is_online`.

---

## Phase 9 — WebSocket

| Step | What | Files | Detail |
|---|---|---|---|
| 9.1 | WS telemetry endpoint | `app/main.py` | `WS /ws/telemetry` — JWT from query param, validate, filter by user's devices, relay telemetry events. |

**Verify:** WebSocket connects with valid token. Closes with 4001 on invalid/expired token.

---

## Phase 10 — Final Verification

| Step | What | Detail |
|---|---|---|
| 10.1 | Update AGENTS.md | New commands, env vars, file list, endpoint docs. |
| 10.2 | Update PLAN.md | Mark completed steps. |
| 10.3 | Full integration smoke test | Start server, verify all endpoints with curl. Publish mock MQTT messages, verify DB updates. |

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Auth0 JWKS fetch fails on startup | Cache JWKS locally, retry with backoff. Log warning, reject all tokens until resolved. |
| Removing old endpoints breaks existing clients | Frontend already updated. No other consumers known. |
| `permisos_usuario_artefacto` JOIN on every request | Single user = few rows. Add index on `(id_usuario, id_artefacto)`. Optimize later. |
| Slowapi rate limit state lost on restart | Acceptable for single-user. Consider Redis backing if needed. |
| WebSocket token expiry mid-connection | Check exp claim periodically, close with 4001 if expired. |