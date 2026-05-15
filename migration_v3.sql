-- ==============================================================================
-- Migration V2.1 -> V3.0
-- Safe to re-run (idempotent checks throughout)
-- ==============================================================================

-- 0. Drop deprecated tables (scaffold data only)
DROP TABLE IF EXISTS permisos_app_artefacto;
DROP TABLE IF EXISTS app_api_keys;

-- 1. Create usuarios table
CREATE TABLE IF NOT EXISTS usuarios (
    id INT AUTO_INCREMENT PRIMARY KEY,
    auth0_id VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) NOT NULL,
    nombre VARCHAR(255),
    fecha_registro DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ultimo_acceso DATETIME NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uc_auth0_id UNIQUE (auth0_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2. Create permisos_usuario_artefacto table
CREATE TABLE IF NOT EXISTS permisos_usuario_artefacto (
    id_usuario INT NOT NULL,
    id_artefacto INT NOT NULL,
    nivel_acceso VARCHAR(20) NOT NULL DEFAULT 'ADMIN',
    fecha_asignacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id_usuario, id_artefacto),
    CONSTRAINT fk_pua_usuario FOREIGN KEY (id_usuario) REFERENCES usuarios(id) ON DELETE CASCADE,
    CONSTRAINT fk_pua_artefacto FOREIGN KEY (id_artefacto) REFERENCES artefactos(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3. Add is_encendido to artefactos (if not exists)
SET @col_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'artefactos' AND COLUMN_NAME = 'is_encendido');
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE artefactos ADD COLUMN is_encendido BOOLEAN NOT NULL DEFAULT FALSE AFTER is_online',
    'SELECT "is_encendido already exists"');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 4. Add id_usuario to eventos_usuario (if not exists)
SET @col_exists2 = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'eventos_usuario' AND COLUMN_NAME = 'id_usuario');
SET @sql2 = IF(@col_exists2 = 0,
    'ALTER TABLE eventos_usuario ADD COLUMN id_usuario INT NULL AFTER id_artefacto',
    'SELECT "id_usuario already exists"');
PREPARE stmt2 FROM @sql2;
EXECUTE stmt2;
DEALLOCATE PREPARE stmt2;

-- 5. Add FK for id_usuario (if not exists)
SET @fk_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'eventos_usuario' AND CONSTRAINT_NAME = 'fk_eventos_usuario');
SET @sql3 = IF(@fk_exists = 0,
    'ALTER TABLE eventos_usuario ADD CONSTRAINT fk_eventos_usuario FOREIGN KEY (id_usuario) REFERENCES usuarios(id) ON DELETE SET NULL',
    'SELECT "fk_eventos_usuario already exists"');
PREPARE stmt3 FROM @sql3;
EXECUTE stmt3;
DEALLOCATE PREPARE stmt3;

-- Verify
SELECT 'Migration V3.0 complete' AS status;
SHOW TABLES;
DESCRIBE usuarios;
DESCRIBE permisos_usuario_artefacto;
DESCRIBE artefactos;
DESCRIBE eventos_usuario;