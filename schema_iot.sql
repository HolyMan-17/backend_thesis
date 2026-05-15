-- ==============================================================================
-- PROYECTO: Smart Mini-UPS & TinyML IoT Gateway (FINAL V3.0)
-- MOTOR: MariaDB
-- DESCRIPCIÓN: Esquema unificado con Telemetría Particionada, Control M2M,
-- Alertas de Sistema, Autenticación Auth0 JWT, y Permisos Usuario-Artefacto.
-- ==============================================================================

-- 1. USUARIOS (Auth0 JWT Authentication)
CREATE TABLE usuarios (
    id INT AUTO_INCREMENT PRIMARY KEY,
    auth0_id VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) NOT NULL,
    nombre VARCHAR(255),
    fecha_registro DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ultimo_acceso DATETIME NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uc_auth0_id UNIQUE (auth0_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2. TABLA NÚCLEO (Hardware & Device Shadow)
CREATE TABLE artefactos (
    id INT AUTO_INCREMENT PRIMARY KEY,
    mac VARCHAR(17) UNIQUE NOT NULL,
    nombre_personalizado VARCHAR(100),
    nivel_prioridad VARCHAR(10) NOT NULL,
    limite_consumo_w DECIMAL(8,2) NOT NULL,
    estado_deseado BOOLEAN NOT NULL DEFAULT FALSE,
    estado_reportado BOOLEAN NOT NULL DEFAULT FALSE,
    is_online BOOLEAN NOT NULL DEFAULT FALSE,
    is_encendido BOOLEAN NOT NULL DEFAULT FALSE,
    last_seen_at DATETIME NULL,
    override_activo BOOLEAN NOT NULL DEFAULT FALSE,
    vencimiento_lease DATETIME NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3. PERMISOS USUARIO -> ARTEFACTO (replaces permisos_app_artefacto)
CREATE TABLE permisos_usuario_artefacto (
    id_usuario INT NOT NULL,
    id_artefacto INT NOT NULL,
    nivel_acceso VARCHAR(20) NOT NULL DEFAULT 'ADMIN',
    fecha_asignacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id_usuario, id_artefacto),
    CONSTRAINT fk_permiso_usuario FOREIGN KEY (id_usuario) REFERENCES usuarios(id) ON DELETE CASCADE,
    CONSTRAINT fk_permiso_artefacto FOREIGN KEY (id_artefacto) REFERENCES artefactos(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4. LOG DE ALERTAS DEL SISTEMA
CREATE TABLE alertas_sistema (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    id_artefacto INT NOT NULL,
    tipo_alerta VARCHAR(50) NOT NULL,
    mensaje VARCHAR(255) NOT NULL,
    severidad VARCHAR(20) NOT NULL,
    leido BOOLEAN NOT NULL DEFAULT FALSE,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_alertas_artefacto FOREIGN KEY (id_artefacto) REFERENCES artefactos(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 5. CREDENCIALES CRIPTOGRÁFICAS M2M
CREATE TABLE credenciales_mtls (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_artefacto INT NOT NULL,
    hash_certificado VARCHAR(255) NOT NULL,
    token_activo VARCHAR(255) NOT NULL,
    fecha_emision DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    estado_revocado BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT fk_credenciales_artefacto FOREIGN KEY (id_artefacto) REFERENCES artefactos(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 6. GESTIÓN DE ACTUALIZACIONES OTA
CREATE TABLE despliegues_ota (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_artefacto INT NOT NULL,
    version_modelo_ml VARCHAR(50) NOT NULL,
    url_descarga VARCHAR(255) NOT NULL,
    hash_firma VARCHAR(255) NOT NULL,
    estado_despliegue VARCHAR(50) NOT NULL,
    fecha_despliegue DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_ota_artefacto FOREIGN KEY (id_artefacto) REFERENCES artefactos(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 7. DATASET DE ENTRENAMIENTO EMBEBIDO
CREATE TABLE eventos_usuario (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    id_artefacto INT NOT NULL,
    id_usuario INT NULL,
    accion VARCHAR(100) NOT NULL,
    razon_disparo VARCHAR(255),
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_eventos_artefacto FOREIGN KEY (id_artefacto) REFERENCES artefactos(id) ON DELETE CASCADE,
    CONSTRAINT fk_eventos_usuario FOREIGN KEY (id_usuario) REFERENCES usuarios(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 8. SERIES TEMPORALES DE ALTA FRECUENCIA
CREATE TABLE telemetria (
    id BIGINT AUTO_INCREMENT,
    id_artefacto INT NOT NULL,
    timestamp DATETIME NOT NULL,
    voltaje DECIMAL(8,2) NOT NULL,
    corriente DECIMAL(8,2) NOT NULL,
    potencia DECIMAL(8,2) NOT NULL,
    tiempo_operacion_s INT NOT NULL,
    estado_sin_cambios BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (id, timestamp) 
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
PARTITION BY RANGE (YEAR(timestamp) * 100 + MONTH(timestamp)) (
    PARTITION p202605 VALUES LESS THAN (202606),
    PARTITION p202606 VALUES LESS THAN (202607),
    PARTITION p202607 VALUES LESS THAN (202608),
    PARTITION p202608 VALUES LESS THAN (202609),
    PARTITION p202609 VALUES LESS THAN (202610),
    PARTITION p202610 VALUES LESS THAN (202611),
    PARTITION p202611 VALUES LESS THAN (202612),
    PARTITION p202612 VALUES LESS THAN (202701),
    PARTITION p_max VALUES LESS THAN MAXVALUE
);

-- ==============================================================================
-- DEPRECATED TABLES (kept for reference; do not add new rows)
-- ==============================================================================

-- DEPRECATED: Use usuarios + permisos_usuario_artefacto instead
-- CREATE TABLE app_api_keys (
--     id INT AUTO_INCREMENT PRIMARY KEY,
--     nombre_app VARCHAR(100) NOT NULL,
--     api_key_hash VARCHAR(255) NOT NULL UNIQUE,
--     fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
--     activa BOOLEAN NOT NULL DEFAULT TRUE
-- ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- DEPRECATED: Use permisos_usuario_artefacto instead
-- CREATE TABLE permisos_app_artefacto (
--     id_api_key INT NOT NULL,
--     id_artefacto INT NOT NULL,
--     nivel_acceso VARCHAR(20) NOT NULL DEFAULT 'ADMIN',
--     fecha_asignacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
--     PRIMARY KEY (id_api_key, id_artefacto),
--     CONSTRAINT fk_permiso_api FOREIGN KEY (id_api_key) REFERENCES app_api_keys(id) ON DELETE CASCADE,
--     CONSTRAINT fk_permiso_artefacto FOREIGN KEY (id_artefacto) REFERENCES artefactos(id) ON DELETE CASCADE
-- ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ==============================================================================
-- MIGRATION NOTES (for existing databases)
-- ==============================================================================
-- 1. CREATE TABLE usuarios (see above)
-- 2. CREATE TABLE permisos_usuario_artefacto (see above)
-- 3. ALTER TABLE eventos_usuario ADD COLUMN id_usuario INT NULL AFTER id_artefacto;
-- 4. ALTER TABLE eventos_usuario ADD CONSTRAINT fk_eventos_usuario FOREIGN KEY (id_usuario) REFERENCES usuarios(id) ON DELETE SET NULL;
-- 5. ALTER TABLE artefactos ADD COLUMN is_encendido BOOLEAN NOT NULL DEFAULT FALSE AFTER is_online;
