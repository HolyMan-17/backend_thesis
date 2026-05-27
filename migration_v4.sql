-- ==============================================================================
-- MIGRATION V3.0 → V4.0
-- SmartSaver IoT Backend
--
-- Changes:
--   1. Add `deleted_at` to artefactos (soft delete)
--   2. Add `limite_voltaje`, `limite_corriente`, `limite_potencia` to artefactos
--   3. Add `resuelto` to alertas_sistema (alert deduplication)
--
-- Idempotent: uses IF NOT EXISTS / safe ALTER patterns.
-- ==============================================================================

-- 1. Soft delete column for devices
ALTER TABLE artefactos
    ADD COLUMN IF NOT EXISTS deleted_at DATETIME NULL AFTER vencimiento_lease;

CREATE INDEX IF NOT EXISTS idx_artefactos_deleted_at ON artefactos(deleted_at);

-- 2. Persistent device limits (voltage, current, power thresholds)
ALTER TABLE artefactos
    ADD COLUMN IF NOT EXISTS limite_voltaje DECIMAL(8,2) NULL AFTER limite_consumo_w,
    ADD COLUMN IF NOT EXISTS limite_corriente DECIMAL(8,2) NULL AFTER limite_voltaje,
    ADD COLUMN IF NOT EXISTS limite_potencia DECIMAL(8,2) NULL AFTER limite_corriente;

-- 3. Alert resolution tracking
ALTER TABLE alertas_sistema
    ADD COLUMN IF NOT EXISTS resuelto BOOLEAN NOT NULL DEFAULT FALSE AFTER leido;

CREATE INDEX IF NOT EXISTS idx_alertas_resuelto ON alertas_sistema(resuelto);