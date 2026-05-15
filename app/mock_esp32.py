import time
import json
import random

import paho.mqtt.client as mqtt

from config import settings

MAC_ESP32 = "00:1B:44:11:3A:B7"

estado_rele_encendido = True
tiempo_operacion_s = 0


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code != 0:
        print(f"[ERROR] Conexion rechazada, codigo: {reason_code}")
        return

    print(f"[OK] ESP32 simulador conectado a {settings.MQTT_HOST}:{settings.MQTT_PORT}")

    client.subscribe(f"smartups/dispositivos/{MAC_ESP32}/comando/#")

    topic_conexion = f"smartups/dispositivos/{MAC_ESP32}/conexion"
    payload_conexion = json.dumps({"is_online": True})
    client.publish(topic_conexion, payload_conexion, qos=1, retain=True)
    print(f"[TX] {topic_conexion} -> is_online=true")

    topic_reporte = f"smartups/dispositivos/{MAC_ESP32}/reporte/estado"
    payload_estado = json.dumps({"encendido": estado_rele_encendido})
    client.publish(topic_reporte, payload_estado, qos=1, retain=False)
    print(f"[TX] {topic_reporte} -> encendido={estado_rele_encendido}")


def on_message(client, userdata, msg):
    global estado_rele_encendido

    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except json.JSONDecodeError:
        print(f"[WARN] Payload invalido en {topic}: {msg.payload}")
        return

    print(f"\n[RX] {topic}: {payload}")

    if "comando/estado" in topic:
        nuevo_estado = payload.get("encendido")
        if nuevo_estado is not None:
            estado_rele_encendido = bool(nuevo_estado)
            estado_str = "ON" if estado_rele_encendido else "OFF"
            print(f"[ACT] Rele -> {estado_str}")

            topic_reporte = f"smartups/dispositivos/{MAC_ESP32}/reporte/estado"
            client.publish(
                topic_reporte,
                json.dumps({"encendido": estado_rele_encendido}),
                qos=1,
            )
            print(f"[TX] {topic_reporte} -> encendido={estado_rele_encendido}")

    elif "comando/limites" in topic:
        print(f"[ACT] Limites actualizados en EEPROM (simulada)")
        topic_reporte = f"smartups/dispositivos/{MAC_ESP32}/reporte/limites"
        client.publish(topic_reporte, json.dumps(payload), qos=1)
        print(f"[TX] {topic_reporte} -> {payload}")


def publish_telemetry(client):
    global tiempo_operacion_s

    if not estado_rele_encendido:
        return

    tiempo_operacion_s += 5

    voltaje = round(random.uniform(11.8, 12.5), 2)
    corriente = round(random.uniform(1.5, 2.0), 2)
    potencia = round(voltaje * corriente, 2)

    telemetria = {
        "mac_dispositivo": MAC_ESP32,
        "voltaje": voltaje,
        "corriente": corriente,
        "potencia": potencia,
        "tiempo_operacion_s": tiempo_operacion_s,
    }

    topic = f"smartups/dispositivos/{MAC_ESP32}/telemetria"
    client.publish(topic, json.dumps(telemetria), qos=1)
    print(f"[TX] {voltaje}V {corriente}A {potencia}W uptime={tiempo_operacion_s}s")


def main():
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="esp32_simulator_1",
    )
    client.on_connect = on_connect
    client.on_message = on_message
    client.username_pw_set(username=settings.MQTT_USER, password=settings.MQTT_PASS)

    topic_conexion = f"smartups/dispositivos/{MAC_ESP32}/conexion"
    client.will_set(
        topic_conexion,
        payload=json.dumps({"is_online": False}),
        qos=1,
        retain=True,
    )

    print(f"[INIT] Conectando a {settings.MQTT_HOST}:{settings.MQTT_PORT} ...")
    client.connect(settings.MQTT_HOST, settings.MQTT_PORT, 60)
    client.loop_start()

    try:
        while True:
            publish_telemetry(client)
            time.sleep(5)
    except KeyboardInterrupt:
        print("\n[STOP] Desconectando simulador...")
    finally:
        client.publish(
            topic_conexion,
            json.dumps({"is_online": False}),
            qos=1,
            retain=True,
        )
        print(f"[TX] {topic_conexion} -> is_online=false")
        client.loop_stop()
        client.disconnect()
        print("[STOP] Desconectado.")


if __name__ == "__main__":
    main()
