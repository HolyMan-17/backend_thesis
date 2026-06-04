import datetime
import unittest

# --- Mock Backend Payloads ---

mock_alerts = [
    {
        "id": 1,
        "tipo_alerta": "sobretension",
        "mensaje": "Voltaje 115.5V excede límite 110V",
        "timestamp": "2026-06-04T12:00:00Z",
        "leido": False,
        "resuelto": False
    },
    {
        "id": 2,
        "tipo_alerta": "bms_critica",
        "mensaje": "BMS Emergency shutdown",
        "timestamp": "2026-06-04T12:05:00Z",
        "leido": True,
        "resuelto": True
    }
]

mock_recommendations = [
    {
        "id": 10,
        "tipo_recomendacion": "consumo_riesgo_sostenido",
        "mensaje": "High risky consumption detected.",
        "timestamp": "2026-06-04T12:10:00Z",
        "resuelto": False
    }
]

mock_events = [
    {
        "id": 100,
        "accion": "comando_estado",
        "id_usuario": None,  # Represents schedule automation
        "razon_disparo": "Relay encendido por horario",
        "timestamp": "2026-06-04T11:50:00Z"
    },
    {
        "id": 101,
        "accion": "comando_estado",
        "id_usuario": 1,  # Represents manual user trigger
        "razon_disparo": "Relay encendido por usuario",
        "timestamp": "2026-06-04T11:55:00Z"
    },
    {
        "id": 102,
        "accion": "auto_kill",
        "id_usuario": None,
        "razon_disparo": "AI auto-kill shutdown execution",
        "timestamp": "2026-06-04T12:02:00Z"
    }
]

# --- Frontend Sync Mapping Logic Implementation in Python ---

def map_alerts(alerts):
    mapped = []
    for alert in alerts:
        title = "⚠️ Alerta del Sistema"
        ta = alert["tipo_alerta"]
        if ta == "sobretension":
            title = "⚡ Sobretensión Detectada"
        elif ta == "sobrecorriente":
            title = "⚠️ Sobrecorriente Detectada"
        elif ta == "sobrepotencia":
            title = "🔌 Sobrepotencia Detectada"
        elif ta == "bms_critica":
            title = "🚨 Alerta Crítica BMS"
            
        mapped.append({
            "id": f"alert_{alert['id']}",
            "title": title,
            "body": alert["mensaje"],
            "timestamp": alert["timestamp"],
            "read": alert["leido"] or alert["resuelto"],
            "backendType": "alerta",
            "backendId": alert["id"]
        })
    return mapped

def map_recommendations(recs):
    mapped = []
    for rec in recs:
        title = "💡 Recomendación IA"
        tr = rec["tipo_recomendacion"]
        if tr == "consumo_riesgo_sostenido":
            title = "⚠️ Consumo de Riesgo IA"
        elif tr == "oscilacion_frecuente":
            title = "🔍 Oscilación Frecuente IA"
        elif tr == "recuperacion_consumo":
            title = "✅ Consumo Normalizado IA"
        elif tr == "fluctuacion_voltaje":
            title = "⚡ Fluctuación de Voltaje IA"
            
        mapped.append({
            "id": f"rec_{rec['id']}",
            "title": title,
            "body": rec["mensaje"],
            "timestamp": rec["timestamp"],
            "read": rec["resuelto"],
            "backendType": "recomendacion",
            "backendId": rec["id"]
        })
    return mapped

def map_events(events):
    mapped = []
    for evt in events:
        # Filter: only schedule automation, auto_kill, and safety_override
        is_schedule = evt["accion"] == "comando_estado" and evt["id_usuario"] is None
        is_autokill = evt["accion"] == "auto_kill"
        is_safety = evt["accion"] == "safety_override"
        
        if not (is_schedule or is_autokill or is_safety):
            continue
            
        title = "⚙️ Evento del Sistema"
        body = evt["razon_disparo"] or ""
        
        if evt["accion"] == "comando_estado":
            encendido = "encendido" in body.lower()
            accion_str = "Encendido" if encendido else "Apagado"
            title = f"⏰ Automatización: {accion_str}"
            body = f"El dispositivo se ha {accion_str.lower()} según el horario programado."
        elif evt["accion"] == "auto_kill":
            title = "⚡ Dispositivo Apagado"
            body = body or "El dispositivo fue apagado automáticamente por la IA debido a consumo excesivo."
            
        mapped.append({
            "id": f"evt_{evt['id']}",
            "title": title,
            "body": body,
            "timestamp": evt["timestamp"],
            "read": True,
            "backendType": "evento",
            "backendId": evt["id"]
        })
    return mapped

def sync_notifications(current_notifications, alerts, recs, events):
    mapped_a = map_alerts(alerts)
    mapped_r = map_recommendations(recs)
    mapped_e = map_events(events)
    
    all_fetched = mapped_a + mapped_r + mapped_e
    
    updated_notifications = list(current_notifications)
    
    for fetched in all_fetched:
        exists = any(
            n for n in current_notifications
            if n.get("backendType") == fetched["backendType"] and n.get("backendId") == fetched["backendId"]
        )
        if not exists:
            updated_notifications.append(fetched)
            
    # Sort descending by timestamp
    updated_notifications.sort(key=lambda x: x["timestamp"], reverse=True)
    return updated_notifications[:50] # Cap at 50 for testing

# --- Unit Test Suite ---

class TestNotificationSync(unittest.TestCase):
    
    def test_filter_silent_events(self):
        # Verify manual user action events are filtered out (we only sync automations/warnings)
        mapped_e = map_events(mock_events)
        actions = [e["title"] for e in mapped_e]
        self.assertIn("⏰ Automatización: Encendido", actions)
        self.assertIn("⚡ Dispositivo Apagado", actions)
        self.assertNotIn("Relay encendido por usuario", [e["body"] for e in mapped_e])

    def test_deduplication(self):
        # Mock pre-existing notification in store
        local_notifications = [
            {
                "id": "alert_1",
                "title": "⚡ Sobretensión Detectada",
                "body": "Voltaje 115.5V excede límite 110V",
                "timestamp": "2026-06-04T12:00:00Z",
                "read": True,
                "backendType": "alerta",
                "backendId": 1
            }
        ]
        
        result = sync_notifications(local_notifications, mock_alerts, mock_recommendations, mock_events)
        
        # Verify that alert 1 was not duplicated (only one instance exists in the result)
        alert_1_count = sum(1 for n in result if n.get("backendType") == "alerta" and n.get("backendId") == 1)
        self.assertEqual(alert_1_count, 1)
        
        # Verify recommendation and automated events are successfully merged in sorted order
        self.assertTrue(len(result) > 1)
        timestamps = [n["timestamp"] for n in result]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

if __name__ == "__main__":
    unittest.main()
