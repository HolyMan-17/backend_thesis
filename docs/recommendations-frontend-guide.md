# SmartSaver Recommendations — Backend Changes & Frontend Integration Guide

## Deployment Checklist (Backend)

### 1. Run the database migration

```bash
sudo mysql iot_telemetry < migration_v7.sql
```

This creates the `recomendaciones` table with the composite index `(id_artefacto, tipo_recomendacion, resuelto)` for fast deduplication lookups.

### 2. Restart the FastAPI service

```bash
sudo systemctl restart fastapi_iot
```

The recommendation engine starts automatically as a background asyncio task in the FastAPI lifespan.

### 3. Optional env overrides (defaults work out of the box)

Add these to `.env` if you want to tune thresholds:

```env
RECOMMENDATION_SCAN_INTERVAL=60
RECOMMENDATION_SUSTAINED_RISKY_MIN=5
RECOMMENDATION_OSCILLATION_WINDOW_MIN=30
RECOMMENDATION_OSCILLATION_THRESHOLD=5
RECOMMENDATION_RECOVERY_SAFE_MIN=5
RECOMMENDATION_VOLTAGE_BROWNOUT=105.0
RECOMMENDATION_VOLTAGE_SAG_COUNT=3
RECOMMENDATION_RECOVERY_LOOKBACK_HOURS=24
```

---

## What Changed on the Backend

### New database table: `recomendaciones`

Mirrors `alertas_sistema` pattern:
- One active recommendation per `(device, tipo_recomendacion)`
- `resuelto` + `resolucion` columns for hybrid resolution (`auto` | `manual`)
- `accion_sugerida` column stores the suggested frontend action (`turn_off` | `investigate` | `null`)
- `severidad` column: `warning` | `info` | `critical`

### New background task: `app/recommendation_engine.py`

Runs every `RECOMMENDATION_SCAN_INTERVAL` seconds (default 60s). Scans all online devices and evaluates four recommendation types:

| Type | Trigger | Action | Auto-resolves when... |
|------|---------|--------|----------------------|
| `consumo_riesgo_sostenido` | avg `ai_status` ≥ 1 over 5+ min (tolerates 1-2 blips to 0) | `turn_off` | avg `ai_status` < 1 over 5 min |
| `oscilacion_frecuente` | 5+ `ai_status` transitions in 30 min | `investigate` | < 2 transitions in 30 min |
| `recuperacion_consumo` | sustained SAFE after recently resolved RISKY episode | `null` (info) | **Never** — user must dismiss |
| `fluctuacion_voltaje` | avg voltage < 105V for 5+ min OR 3+ discrete sag events in 30 min | `turn_off` | avg voltage > 105V for 5+ min |

**Key design decisions:**
- **Sustained RISKY** uses the average over the window, not consecutive readings. A single SAFE blip doesn't break the streak.
- **Oscillation** counts any `ai_status` change (0↔1, 1↔2, 0↔2) as a transition.
- **Recovery** only fires if there was a recently auto-resolved `consumo_riesgo_sostenido` within the last 24h.
- **Voltage sags** count discrete events (transition from ≥105V to <105V), not every below-threshold reading.

### New REST endpoints

```
GET  /api/recomendaciones?solo_activas=true
PATCH /api/recomendaciones/{id}
```

Both require JWT Bearer auth.

### Response shape: `RecomendacionResponse`

```json
{
  "id": 1,
  "id_artefacto": 3,
  "tipo_recomendacion": "consumo_riesgo_sostenido",
  "mensaje": "Living Room Light shows sustained risky consumption (avg AI status: 1.2) for 5+ min. Consider turning it off to preserve battery life.",
  "accion_sugerida": "turn_off",
  "severidad": "warning",
  "resuelto": false,
  "resolucion": null,
  "timestamp": "2024-05-28T14:32:00Z",
  "resuelto_en": null,
  "mac_dispositivo": "AA:BB:CC:DD:EE:FF",
  "nombre_personalizado": "Living Room Light"
}
```

`accion_sugerida` values the frontend must handle:
- `"turn_off"` → show a "Turn Off" button that calls `POST /api/dispositivos/{mac}/comando/estado {"encendido": false}`
- `"investigate"` → show an informational "Investigate" label or link to device detail
- `null` → no action button (informational only)

---

## Frontend Integration Requirements

### 1. Recommendations list screen

**API:** `GET /api/recomendaciones?solo_activas=true`

- Show active recommendations in a card/list format.
- Display `mensaje` as the primary text.
- Use `nombre_personalizado` (or fall back to `mac_dispositivo`) to identify the device.
- Show a **dismiss** action for every recommendation: `PATCH /api/recomendaciones/{id} {"resuelto": true}`.
- Conditionally render action buttons based on `accion_sugerida`:
  - `turn_off` → "Turn Off Device" button
  - `investigate` → "Investigate" label/button linking to device detail
  - `null` → no action button

**Severity styling:**
- `warning` → yellow/amber badge
- `info` → blue badge
- `critical` → red badge (not currently emitted by recommendations, but reserved)

### 2. Dismissal behavior

When user taps dismiss:
```javascript
fetch('/api/recomendaciones/42', {
  method: 'PATCH',
  headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
  body: JSON.stringify({ resuelto: true })
})
```

The recommendation is immediately removed from the active list (`resolucion` becomes `"manual"`).

### 3. Auto-resolution awareness

Recommendations can disappear from the active list without user action (auto-resolved by the backend when the condition clears). The frontend should not crash if a recommendation it was displaying suddenly returns 404 on dismissal — just remove it from the UI.

### 4. Turn-off action flow

For `accion_sugerida: "turn_off"`:
1. User taps "Turn Off" on the recommendation card.
2. Frontend calls `POST /api/dispositivos/{mac}/comando/estado` with `{"encendido": false}`.
3. On success, the device state updates via WebSocket (`reporte/estado`) or the frontend can optimistically update.
4. The recommendation itself does NOT auto-dismiss when the device is turned off — the backend will auto-resolve it on the next scan cycle (up to 60s delay). The frontend can optionally auto-dismiss it locally for better UX.

### 5. Badge / notification indicator

The frontend can poll `GET /api/recomendaciones?solo_activas=true` periodically (e.g., every 30s or on screen focus) to show an unread count badge. There is **no WebSocket push** for recommendations in V1.

### 6. Historical view (optional)

Pass `?solo_activas=false` to see resolved recommendations. Useful for a "History" tab. Show `resolucion` label:
- `auto` → "Auto-resolved"
- `manual` → "Dismissed by user"

---

## Testing Tips

### Simulate a sustained RISKY recommendation

Use the mock ESP32 with `ai_status=1` for 5+ minutes:
```python
# In app/mock_esp32.py, modify the telemetry payload to include:
"ai_status": 1
```

Or send telemetry manually:
```bash
curl -X POST http://localhost:8000/api/telemetria \
  -H "Content-Type: application/json" \
  -d '{
    "mac_dispositivo": "AA:BB:CC:DD:EE:FF",
    "voltaje": 115.0,
    "corriente": 2.5,
    "potencia": 280.0,
    "tiempo_operacion_s": 300,
    "ai_status": 1
  }'
```

Send 6+ readings within 5 minutes with `ai_status: 1`. The recommendation will appear on the next scan cycle (within 60s).

### Simulate voltage fluctuation

Send telemetry with voltage < 105V:
```bash
curl -X POST http://localhost:8000/api/telemetria \
  -H "Content-Type: application/json" \
  -d '{
    "mac_dispositivo": "AA:BB:CC:DD:EE:FF",
    "voltaje": 100.0,
    "corriente": 1.0,
    "potencia": 100.0,
    "tiempo_operacion_s": 300,
    "ai_status": 0
  }'
```

Send 3+ readings with voltage dipping below 105V within 30 minutes. The `fluctuacion_voltaje` recommendation will trigger.

### Check engine logs

```bash
sudo journalctl -u fastapi_iot -f
```

Look for: `Recommendation engine started` and scan error messages.

---

## Rollback Plan

If needed, the migration is idempotent (`CREATE TABLE IF NOT EXISTS`). To disable the engine without code changes, set `RECOMMENDATION_SCAN_INTERVAL=999999` in `.env` and restart. To fully remove, drop the table:

```sql
DROP TABLE IF EXISTS recomendaciones;
```

And remove the engine import/wiring from `app/main.py`.