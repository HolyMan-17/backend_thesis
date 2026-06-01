-- migration_v10.sql
-- V9.0 -> V10.0
-- Agrega columna expo_push_token a la tabla usuarios para notificaciones push

ALTER TABLE usuarios 
ADD COLUMN expo_push_token VARCHAR(255) DEFAULT NULL;
