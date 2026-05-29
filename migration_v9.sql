-- migration_v9.sql
-- V8.0 -> V9.0
-- Agrega la tabla de horarios para automatización de dispositivos

CREATE TABLE IF NOT EXISTS artefactos_horarios (
    id_artefacto INT PRIMARY KEY,
    dias_operacion JSON NOT NULL,
    hora_encendido TIME DEFAULT NULL,
    hora_apagado TIME DEFAULT NULL,
    automatizacion_activa BOOLEAN DEFAULT FALSE,
    actualizado_en DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_horario_artefacto FOREIGN KEY (id_artefacto) REFERENCES artefactos(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
