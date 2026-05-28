-- Migration V7.0 → V8.0
-- Adds AI control fields: user-level global toggles + device-level scheduling state

-- User-level global settings (apply to ALL devices owned by the user)
ALTER TABLE usuarios
    ADD COLUMN ai_control_habilitado BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN auto_apagado_low_priority BOOLEAN NOT NULL DEFAULT FALSE;

-- Device-level scheduling state (each device has its own auto-kill timer and override)
ALTER TABLE artefactos
    ADD COLUMN auto_kill_at TIMESTAMP NULL DEFAULT NULL,
    ADD COLUMN ai_override_until TIMESTAMP NULL DEFAULT NULL;