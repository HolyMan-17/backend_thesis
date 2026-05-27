-- =============================================================================
-- MIGRACIÓN V4.0 → V5.0
-- DESCRIPCIÓN: Normaliza límites a artefactos_limites, elimina is_encendido
--              duplicado, consolida device shadow (estado_deseado/reportado).
--
-- IDEMPOTENT: Can be run multiple times. Checks column existence before
-- migrating data, skips steps that have already been applied.
-- =============================================================================

-- 1. Crear tabla de límites normalizada (1:1 con artefactos)
CREATE TABLE IF NOT EXISTS artefactos_limites (
    id_artefacto INT PRIMARY KEY,
    limite_consumo_w DECIMAL(8,2) NOT NULL DEFAULT 0,
    limite_voltaje DECIMAL(8,2) NULL,
    limite_corriente DECIMAL(8,2) NULL,
    limite_potencia DECIMAL(8,2) NULL,
    actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_limites_artefacto FOREIGN KEY (id_artefacto) REFERENCES artefactos(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2. Migrar datos existentes (only if old columns still exist in artefactos)
--    Uses a prepared statement to handle the case where columns were already dropped.
SET @col_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'artefactos' AND COLUMN_NAME = 'is_encendido');

SET @migrate_sql = IF(@col_exists > 0,
    'INSERT INTO artefactos_limites (id_artefacto, limite_consumo_w, limite_voltaje, limite_corriente, limite_potencia)
     SELECT id, COALESCE(limite_consumo_w, 0), limite_voltaje, limite_corriente, limite_potencia
     FROM artefactos
     WHERE id NOT IN (SELECT id_artefacto FROM artefactos_limites)',
    'SELECT 1 WHERE 1=0'
);

PREPARE stmt FROM @migrate_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 3. Eliminar columnas de artefactos (idempotente)
ALTER TABLE artefactos DROP COLUMN IF EXISTS limite_consumo_w;
ALTER TABLE artefactos DROP COLUMN IF EXISTS limite_voltaje;
ALTER TABLE artefactos DROP COLUMN IF EXISTS limite_corriente;
ALTER TABLE artefactos DROP COLUMN IF EXISTS limite_potencia;
ALTER TABLE artefactos DROP COLUMN IF EXISTS is_encendido;