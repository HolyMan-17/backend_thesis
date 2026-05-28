# SmartSaver — AI Control & Auto-Kill: Frontend Integration Guide

## Deployment Checklist

```bash
sudo mysql iot_telemetry < migration_v7.sql   # Recommendations table (if not already applied)
sudo mysql iot_telemetry < migration_v8.sql   # AI control fields
sudo systemctl restart fastapi_iot
```

New env vars (defaults work out of the box):

```env
AI_CONTROL_RISKY_THRESHOLD_MIN=2
AI_CONTROL_GRACE_PERIOD_MIN=5
AI_CONTROL_OVERRIDE_COOLDOWN_MIN=30
```

---

## Architecture Summary

**AI Control toggles are GLOBAL per-user settings**, not per-device. A single toggle in the app settings screen controls whether the AI can manage all the user's devices.

| Setting | Stored On | Scope | Default |
|---------|-----------|-------|---------|
| `ai_control_habilitado` | `usuarios` table | All devices owned by user | `false` |
| `auto_apagado_low_priority` | `usuarios` table | All P3 devices owned by user | `false` |
| `auto_kill_at` | `artefactos` table | Per-device (scheduled kill time) | `null` |
| `ai_override_until` | `artefactos` table | Per-device (override cooldown) | `null` |

---

## New API Endpoints

### GET /api/users/settings

Returns the authenticated user's global AI control settings.

```json
{
  "ai_control_habilitado": false,
  "auto_apagado_low_priority": false
}
```

### PATCH /api/users/settings

Update global AI control toggles. Both fields are optional — only send what you want to change.

```json
// Enable Master AI Control
PATCH /api/users/settings
{ "ai_control_habilitado": true }

// Enable P3 auto-kill
PATCH /api/users/settings
{ "auto_apagado_low_priority": true }

// Both at once
PATCH /api/users/settings
{ "ai_control_habilitado": true, "auto_apagado_low_priority": true }
```

Response is the same shape as GET.

### PATCH /api/dispositivos/{mac}

No longer accepts `ai_control_habilitado` or `auto_apagado_low_priority`. These are now user-level settings.

The device response now includes `auto_kill_at` (nullable ISO8601 timestamp):

```json
{
  "...existing fields...",
  "auto_kill_at": "2024-05-28T14:37:00Z"   // or null if no pending auto-kill
}
```

### POST /api/dispositivos/{mac}/ai-control/override

Unchanged. Cancels a pending auto-kill for a specific device and starts a 30-minute cooldown.

```json
// Response
{
  "status": "overridden",
  "mac": "AA:BB:CC:DD:EE:FF",
  "ai_override_until": "2024-05-28T15:07:00Z"
}
```

---

## Frontend Integration

### 1. App Settings Screen — Global AI Control Toggles

Add a **global settings section** (not per-device) with two toggles:

#### Toggle 1: "Allow AI to manage my devices" (`ai_control_habilitado`)

| Field | Value |
|-------|-------|
| Location | App settings screen (global, not per-device) |
| Label | "Allow AI to manage my devices" |
| Description | "When enabled, the AI can automatically turn off devices after a warning period if it detects sustained high power consumption." |
| API | `PATCH /api/users/settings { "ai_control_habilitado": true/false }` |
| Default | `false` |

#### Toggle 2: "Auto-turn off low-priority devices" (`auto_apagado_low_priority`)

| Field | Value |
|-------|-------|
| Location | App settings screen (global, not per-device) |
| Label | "Auto-turn off low-priority devices" |
| Description | "When enabled, any device set to P3 (low priority) will be immediately turned off when risky consumption is detected — no warning period." |
| API | `PATCH /api/users/settings { "auto_apagado_low_priority": true/false }` |
| Default | `false` |
| Note | Independent of the "Allow AI" toggle. Works on its own. |

**Important:** These toggles apply to ALL devices the user owns. They are NOT per-device settings.

```typescript
// Fetch current settings
const response = await fetch('/api/users/settings', {
  headers: { Authorization: `Bearer ${token}` },
});
const settings = await response.json();

// Enable Master AI Control
await fetch('/api/users/settings', {
  method: 'PATCH',
  headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
  body: JSON.stringify({ ai_control_habilitado: true }),
});
```

### 2. Device Detail — Auto-Kill Countdown

When a device has `auto_kill_at` set (not null), show a countdown in the device detail screen:

```typescript
// Check from device response
if (device.auto_kill_at) {
  const timeRemaining = new Date(device.auto_kill_at) - new Date();
  // Show: "Auto-turn off in 4:32" with a "Keep it On" button
}
```

**"Keep it On" button** calls:
```typescript
const response = await fetch(`/api/dispositivos/${mac}/ai-control/override`, {
  method: 'POST',
  headers: { Authorization: `Bearer ${token}` },
});
```

### 3. WebSocket Events — Auto-Kill Flow

Add handling for these WebSocket event types:

| Event | When | Payload | Frontend Action |
|-------|------|---------|------------------|
| `auto_kill_warning` | Grace period started | `{auto_kill_at, grace_period_min, message, accion_sugerida: "keep_on"}` | Show push notification with "Keep it On" button, start countdown |
| `auto_kill_executed` | Device was auto-killed | `{message}` | Show notification "Device was turned off", update device state |
| `auto_kill_cancelled` | Risk cleared before kill | `{message}` | Dismiss countdown, show "Risk cleared" notification |

### 4. Complete Flow Diagram

```
Device sends telemetry with ai_status=1 (RISKY)
         │
         ▼
Recommendation engine detects sustained RISKY (2+ min)
         │
         ├─ Neither setting enabled ──► Passive recommendation only
         │
         ├─ auto_apagado_low_priority=TRUE AND device=P3 ──► Immediate auto-kill
         │       │
         │       ▼
         │   Publish MQTT {"encendido": false}
         │   Push WS: auto_kill_executed
         │   Log event: auto_kill
         │
         └─ ai_control_habilitado=TRUE ──► Start 5-min grace period
                 │
                 ▼
            Set auto_kill_at = NOW() + 5min
            Push WS: auto_kill_warning
                 │
       ┌─────────┴─────────┐
   User overrides      Grace period expires
   (Keep it On)             │
       │                    ▼
       ▼          Publish MQTT {"encendido": false}
   POST override    Push WS: auto_kill_executed
   Clear auto_kill_at  Log event: auto_kill
   + 30min cooldown
       │
   Risk clears before kill ──► Clear auto_kill_at
                                 Push WS: auto_kill_cancelled
```

### 5. Priority-Based Auto-Kill Matrix

| Priority | `ai_control_habilitado` | `auto_apagado_low_priority` | Behavior when RISKY for 2min |
|----------|------------------------|----------------------------|------------------------------|
| P1 | false | false | Passive recommendation only |
| P1 | true | false | 5-min grace period, then auto-kill |
| P1 | false | true | Passive recommendation (not P3) |
| P1 | true | true | 5-min grace period (P3 rule doesn't apply to P1) |
| P2 | false | false | Passive recommendation only |
| P2 | true | false | 5-min grace period, then auto-kill |
| P2 | false | true | Passive recommendation (not P3) |
| P3 | false | false | Passive recommendation only |
| P3 | false | true | **Immediate auto-kill** (no grace period) |
| P3 | true | false | 5-min grace period, then auto-kill |
| P3 | true | true | **Immediate auto-kill** (P3 rule takes precedence) |

### 6. Settings Screen UI Mockup

```
┌─────────────────────────────────────┐
│  ⚙️ Settings                        │
│                                     │
│  ── AI Management ──                │
│                                     │
│  [🔧] Allow AI to manage my devices │
│  When on, AI can auto-turn off      │
│  devices after a 5-min warning.     │
│                          [  OFF  ]  │
│                                     │
│  [🔋] Auto-turn off low-priority   │
│  P3 devices will be turned off      │
│  immediately when risky.            │
│  (Works independently from above)   │
│                          [  OFF  ]  │
│                                     │
└─────────────────────────────────────┘
```

### 7. Testing Checklist

1. **Enable AI control:** `PATCH /api/users/settings { "ai_control_habilitado": true }`
2. **Verify settings:** `GET /api/users/settings` → `{ "ai_control_habilitado": true, "auto_apagado_low_priority": false }`
3. **Send RISKY telemetry** for 2+ min → verify `auto_kill_warning` WebSocket event
4. **Verify `auto_kill_at`** appears in device response
5. **Test override:** `POST /api/dispositivos/{mac}/ai-control/override` → verify `auto_kill_at` is null, `ai_override_until` is set
6. **Test auto-kill execution:** Wait 5 min without override → verify device turns off
7. **Test P3 auto-kill:** Set `auto_apagado_low_priority: true`, set device to P3, send RISKY telemetry → verify immediate auto-kill
8. **Test independence:** Enable only `auto_apagado_low_priority` without `ai_control_habilitado` → P3 auto-kill should still work