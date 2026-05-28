-- Migration V6.0 → V7.0
-- Adds recomendaciones table for AI-based usage recommendations
-- Types: consumo_riesgo_sostenido, oscilacion_frecuente, recuperacion_consumo, fluctuacion_voltaje
-- Mirrors alertas_sistema pattern: one active recommendation per (device, type), hybrid resolution

CREATE TABLE IF NOT EXISTS recomendaciones (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    id_artefacto INT NOT NULL,
    tipo_recomendacion VARCHAR(50) NOT NULL,
    mensaje VARCHAR(500) NOT NULL,
    accion_sugerida VARCHAR(50) DEFAULT NULL,
    severidad VARCHAR(20) NOT NULL DEFAULT 'warning',
    resuelto BOOLEAN NOT NULL DEFAULT FALSE,
    resolucion VARCHAR(20) DEFAULT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resuelto_en TIMESTAMP NULL DEFAULT NULL,
    INDEX idx_recomendaciones_artefacto (id_artefacto),
    INDEX idx_recomendaciones_tipo (tipo_recomendacion),
    INDEX idx_recomendaciones_resuelto (resuelto),
    INDEX idx_recomendaciones_artefacto_tipo_activo (id_artefacto, tipo_recomendacion, resuelto),
    CONSTRAINT fk_recomendaciones_artefacto FOREIGN KEY (id_artefacto) REFERENCES artefactos(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;