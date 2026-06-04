-- migration_v11.sql
-- V10.0 -> V11.0
-- Agrega la tabla de notificaciones de usuario para almacenar el historial de notificaciones enviadas

CREATE TABLE IF NOT EXISTS notificaciones_usuario (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_usuario INT NOT NULL,
    titulo VARCHAR(255) NOT NULL,
    cuerpo TEXT NOT NULL,
    leido BOOLEAN DEFAULT FALSE,
    eliminado BOOLEAN DEFAULT FALSE,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_notif_usuario FOREIGN KEY (id_usuario) REFERENCES usuarios(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Índice para búsquedas rápidas ordenadas por fecha por usuario
CREATE INDEX idx_notif_usuario_lookup ON notificaciones_usuario (id_usuario, eliminado, timestamp DESC);
