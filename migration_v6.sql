-- Migration V5.0 → V6.0
-- Adds ai_status column to telemetria (Edge-AI BMS classification state)
-- 0 = SAFE, 1 = RISKY, 2 = CRITICAL

ALTER TABLE telemetria
    ADD COLUMN ai_status INT NOT NULL DEFAULT 0;