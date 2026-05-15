import os
import json
import asyncio
import paho.mqtt.client as mqtt
from app.database import AsyncSessionLocal
from app.crud import (
    crear_telemetria, 
    actualizar_estado_dispositivo, 
    actualizar_online_dispositivo,
    crear_dispositivo_o_artefacto  # Imported the new provisioning function
)
from app.schemas import TelemetriaCreate

from app.config import settings

_main_loop = None

def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print(f"🔌 Worker {os.getpid()} de FastAPI conectado a Mosquitto", flush=True)
        client.subscribe("smartups/dispositivos/+/telemetria")
        client.subscribe("smartups/dispositivos/+/conexion")
        client.subscribe("smartups/dispositivos/+/reporte/estado")
        client.subscribe("smartups/dispositivos/+/reporte/limites")
        
        # NUEVO: Suscripción al tópico de provisionamiento para atrapar PairRequests
        client.subscribe("smartups/dispositivos/+/provisionamiento")
    else:
        print(f"❌ Error conectando Worker {os.getpid()}. Código: {reason_code}", flush=True)

def on_message(client, userdata, msg):
    payload = msg.payload.decode('utf-8')
    topic = msg.topic
    
    # Asegurar que el hilo de eventos de FastAPI está vivo antes de inyectar la tarea
    if _main_loop and _main_loop.is_running():
        asyncio.run_coroutine_threadsafe(procesar_payload(topic, payload), _main_loop)

async def procesar_payload(topic: str, payload: str):
    try:
        # Desestructuración del tópico
        partes_topic = topic.split("/")
        if len(partes_topic) < 4:
            return # Ignorar tópicos malformados
            
        mac_desde_topic = partes_topic[2]
        tipo_mensaje = partes_topic[3] 
        
        data = json.loads(payload)

        # El Context Manager asegura que la conexión a MariaDB se cierre incluso si hay un error
        async with AsyncSessionLocal() as db:
            if tipo_mensaje == "telemetria":
                telemetria_in = TelemetriaCreate(**data)
                
                if telemetria_in.mac_dispositivo != mac_desde_topic:
                    print(f"⚠️ Anomalía Worker {os.getpid()}: MAC rechazada.", flush=True)
                    return
                
                nueva_metrica = await crear_telemetria(db, telemetria_in)
                if nueva_metrica is not None:
                    print(f"💾 Telemetría guardada | Worker {os.getpid()} | {telemetria_in.potencia}W", flush=True)
                else:
                    print(f"⚠️ Worker {os.getpid()}: Artefacto no provisionado ignorado ({mac_desde_topic}).", flush=True)
            
            elif tipo_mensaje == "conexion":
                estado_actual = data.get("is_online")
                
                if estado_actual is not None:
                    estado_bool = bool(estado_actual)
                    await actualizar_online_dispositivo(db, mac_desde_topic, online=estado_bool)
                    
                    estado_str = "Online 🟢" if estado_bool else "Offline 🔴"
                    print(f"🔄 Estado de {mac_desde_topic} -> {estado_str} | Worker {os.getpid()}", flush=True)
            
            elif tipo_mensaje == "reporte":
                subtipo = partes_topic[4] if len(partes_topic) > 4 else ""
                
                if subtipo == "estado":
                    encendido = data.get("encendido")
                    if encendido is not None:
                        await actualizar_estado_dispositivo(db, mac_desde_topic, encendido=bool(encendido))
                        estado_str = "ON 🟢" if encendido else "OFF 🔴"
                        print(f"📡 Reporte estado {mac_desde_topic} -> {estado_str} | Worker {os.getpid()}", flush=True)
                
                elif subtipo == "limites":
                    print(f"📡 Reporte límites {mac_desde_topic} -> {data} | Worker {os.getpid()}", flush=True)
            
            # NUEVO: Lógica de Provisionamiento vía MQTT
            elif tipo_mensaje == "provisionamiento":
                print(f"🆕 Solicitud de provisionamiento MQTT recibida para {mac_desde_topic} | Worker {os.getpid()}", flush=True)
                
                # Verificar que la MAC del payload coincida con la del tópico para evitar inyecciones
                mac_payload = data.get("mac")
                if mac_payload and mac_payload == mac_desde_topic:
                    # Ejecuta la lógica de BD previamente manejada por la ruta REST
                    await crear_dispositivo_o_artefacto(db, mac_desde_topic)
                    print(f"✅ Artefacto {mac_desde_topic} provisionado exitosamente.", flush=True)
                else:
                    print(f"⚠️ Anomalía en provisionamiento: MAC del payload no coincide.", flush=True)

    except Exception as e:
        print(f"❌ Error en Worker {os.getpid()}: {e}", flush=True)

def iniciar_oyente_mqtt():
    global _main_loop
    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        print("⚠️ Advertencia: No se detectó un event loop de asyncio en ejecución.", flush=True)
        return None
    
    pid_actual = os.getpid()
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"FastAPI_Consumidor_{pid_actual}",
    )
    
    client.username_pw_set(settings.MQTT_USER, settings.MQTT_PASS)
    client.on_connect = on_connect
    client.on_message = on_message
    
    client.connect(settings.MQTT_HOST, settings.MQTT_PORT, 60)
    
    # loop_start() spawns a separate background thread handled by the C/Python GIL
    client.loop_start()
    return client
