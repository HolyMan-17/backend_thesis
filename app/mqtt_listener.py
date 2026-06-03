import os
import json
import asyncio
import paho.mqtt.client as mqtt
from app.database import AsyncSessionLocal
from app.crud import (
    crear_telemetria,
    actualizar_estado_reportado,
    actualizar_online_dispositivo,
    crear_dispositivo_o_artefacto,
    obtener_dispositivo_por_mac,
    obtener_dispositivo_por_telemetria,
    actualizar_dispositivo,
    verificar_cambio_online,
    crear_alerta_si_necesario,
    resolver_alertas_por_tipo,
    crear_evento,
    romper_lease_por_seguridad,
    emergencia_bms_shutdown,
    enviar_push_a_duenos,
)
from app.schemas import TelemetriaCreate
from app.config import settings

_main_loop = None


async def _broadcast_telemetry(mac: str, data: dict):
    try:
        from app.ws_manager import ws_manager
        await ws_manager.broadcast_telemetry(mac, data)
    except Exception:
        pass


async def _broadcast_event(mac: str, event_type: str, data: dict):
    try:
        from app.ws_manager import ws_manager
        await ws_manager.broadcast_event(mac, event_type, data)
    except Exception:
        pass


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print(f"🔌 Worker {os.getpid()} de FastAPI conectado a Mosquitto", flush=True)
        client.subscribe("smartups/dispositivos/+/telemetria")
        client.subscribe("smartups/dispositivos/+/conexion")
        client.subscribe("smartups/dispositivos/+/reporte/estado")
        client.subscribe("smartups/dispositivos/+/reporte/limites")
        client.subscribe("smartups/dispositivos/+/provisionamiento")
        client.subscribe("smartups/dispositivos/+/alerta")
    else:
        print(f"❌ Error conectando Worker {os.getpid()}. Código: {reason_code}", flush=True)


async def _verificar_alertas(db, artefacto, telemetria_in):
    limites = artefacto.limites
    if not limites:
        return

    if limites.limite_voltaje is not None and telemetria_in.voltaje > float(limites.limite_voltaje):
        alerta = await crear_alerta_si_necesario(
            db, artefacto.id, "sobretension",
            f"Voltaje {telemetria_in.voltaje:.2f}V excede límite {float(limites.limite_voltaje):.2f}V",
            "alta",
        )
        if alerta:
            await romper_lease_por_seguridad(db, artefacto.mac)
            await crear_evento(
                db, id_artefacto=artefacto.id,
                accion="safety_override",
                razon_disparo=f"Sobretensión {telemetria_in.voltaje:.2f}V rompe lease de usuario",
            )
            await enviar_push_a_duenos(
                db, artefacto.mac,
                "⚡ Límite de Consumo Excedido",
                f"El dispositivo ha sido apagado de emergencia debido a: Voltaje ({telemetria_in.voltaje:.2f}V > {float(limites.limite_voltaje):.2f}V)."
            )
    else:
        await resolver_alertas_por_tipo(db, artefacto.id, "sobretension")

    if limites.limite_corriente is not None and telemetria_in.corriente > float(limites.limite_corriente):
        alerta = await crear_alerta_si_necesario(
            db, artefacto.id, "sobrecorriente",
            f"Corriente {telemetria_in.corriente:.2f}A excede límite {float(limites.limite_corriente):.2f}A",
            "alta",
        )
        if alerta:
            await romper_lease_por_seguridad(db, artefacto.mac)
            await crear_evento(
                db, id_artefacto=artefacto.id,
                accion="safety_override",
                razon_disparo=f"Sobrecorriente {telemetria_in.corriente:.2f}A rompe lease de usuario",
            )
            await enviar_push_a_duenos(
                db, artefacto.mac,
                "⚡ Límite de Consumo Excedido",
                f"El dispositivo ha sido apagado de emergencia debido a: Corriente ({telemetria_in.corriente:.2f}A > {float(limites.limite_corriente):.2f}A)."
            )
    else:
        await resolver_alertas_por_tipo(db, artefacto.id, "sobrecorriente")

    if limites.limite_potencia is not None and telemetria_in.potencia > float(limites.limite_potencia):
        alerta = await crear_alerta_si_necesario(
            db, artefacto.id, "sobrepotencia",
            f"Potencia {telemetria_in.potencia:.2f}W excede límite {float(limites.limite_potencia):.2f}W",
            "alta",
        )
        if alerta:
            await romper_lease_por_seguridad(db, artefacto.mac)
            await crear_evento(
                db, id_artefacto=artefacto.id,
                accion="safety_override",
                razon_disparo=f"Sobrepotencia {telemetria_in.potencia:.2f}W rompe lease de usuario",
            )
            await enviar_push_a_duenos(
                db, artefacto.mac,
                "⚡ Límite de Consumo Excedido",
                f"El dispositivo ha sido apagado de emergencia debido a: Potencia ({telemetria_in.potencia:.2f}W > {float(limites.limite_potencia):.2f}W)."
            )
    elif limites.limite_consumo_w > 0 and telemetria_in.potencia > float(limites.limite_consumo_w):
        alerta = await crear_alerta_si_necesario(
            db, artefacto.id, "sobrepotencia",
            f"Potencia {telemetria_in.potencia:.2f}W excede consumo límite {float(limites.limite_consumo_w):.2f}W",
            "media",
        )
        if alerta:
            await enviar_push_a_duenos(
                db, artefacto.mac,
                "⚠️ Alerta de Consumo Alto",
                f"La potencia de {telemetria_in.potencia:.2f}W excede el consumo límite configurado de {float(limites.limite_consumo_w):.2f}W."
            )
    else:
        await resolver_alertas_por_tipo(db, artefacto.id, "sobrepotencia")


async def procesar_payload(topic: str, payload: str):
    try:
        partes_topic = topic.split("/")
        if len(partes_topic) < 4:
            return

        mac_desde_topic = partes_topic[2]
        tipo_mensaje = partes_topic[3]

        data = json.loads(payload)

        async with AsyncSessionLocal() as db:
            if tipo_mensaje == "telemetria":
                telemetria_in = TelemetriaCreate(**data)

                if telemetria_in.mac_dispositivo != mac_desde_topic:
                    print(f"⚠️ Anomalía Worker {os.getpid()}: MAC rechazada.", flush=True)
                    return

                nueva_metrica = await crear_telemetria(db, telemetria_in)
                if nueva_metrica is not None:
                    print(f"💾 Telemetría guardada | Worker {os.getpid()} | {telemetria_in.potencia}W", flush=True)

                    artefacto = await obtener_dispositivo_por_telemetria(db, mac_desde_topic)
                    if artefacto:
                        await _verificar_alertas(db, artefacto, telemetria_in)

                    await _broadcast_telemetry(mac_desde_topic, data)
                else:
                    print(f"⚠️ Worker {os.getpid()}: Artefacto no provisionado ignorado ({mac_desde_topic}).", flush=True)

            elif tipo_mensaje == "conexion":
                estado_actual = data.get("is_online")

                if estado_actual is not None:
                    estado_bool = bool(estado_actual)
                    cambio = await verificar_cambio_online(db, mac_desde_topic, estado_bool)
                    await actualizar_online_dispositivo(db, mac_desde_topic, online=estado_bool)

                    if cambio:
                        accion = "conexion_online" if estado_bool else "conexion_offline"
                        razon = "Dispositivo conectado" if estado_bool else "Dispositivo desconectado"
                        dispositivo = await obtener_dispositivo_por_mac(db, mac_desde_topic)
                        if dispositivo:
                            await crear_evento(db, id_artefacto=dispositivo.id, accion=accion, razon_disparo=razon)
                        await _broadcast_event(mac_desde_topic, "conexion", {"is_online": estado_bool})

                    estado_str = "Online 🟢" if estado_bool else "Offline 🔴"
                    print(f"🔄 Estado de {mac_desde_topic} -> {estado_str} | Worker {os.getpid()}", flush=True)

            elif tipo_mensaje == "reporte":
                subtipo = partes_topic[4] if len(partes_topic) > 4 else ""

                if subtipo == "estado":
                    encendido = data.get("encendido")
                    if encendido is not None:
                        await actualizar_estado_reportado(db, mac_desde_topic, encendido=bool(encendido))
                        dispositivo = await obtener_dispositivo_por_mac(db, mac_desde_topic)
                        if dispositivo:
                            await crear_evento(
                                db,
                                id_artefacto=dispositivo.id,
                                accion="reporte_estado",
                                razon_disparo=f"Relay {'encendido' if encendido else 'apagado'}",
                            )
                        estado_str = "ON 🟢" if encendido else "OFF 🔴"
                        print(f"📡 Reporte estado {mac_desde_topic} -> {estado_str} | Worker {os.getpid()}", flush=True)

                elif subtipo == "limites":
                    datos_actualizar = {}
                    if "limite_consumo_w" in data and data["limite_consumo_w"] is not None:
                        datos_actualizar["limite_consumo_w"] = data["limite_consumo_w"]
                    if "limite_voltaje" in data and data["limite_voltaje"] is not None:
                        datos_actualizar["limite_voltaje"] = data["limite_voltaje"]
                    if "limite_corriente" in data and data["limite_corriente"] is not None:
                        datos_actualizar["limite_corriente"] = data["limite_corriente"]
                    if "limite_potencia" in data and data["limite_potencia"] is not None:
                        datos_actualizar["limite_potencia"] = data["limite_potencia"]

                    if datos_actualizar:
                        await actualizar_dispositivo(db, mac_desde_topic, datos_actualizar)
                        print(f"📡 Límites persistidos {mac_desde_topic} -> {datos_actualizar} | Worker {os.getpid()}", flush=True)

            elif tipo_mensaje == "provisionamiento":
                print(f"🆕 Solicitud de provisionamiento MQTT recibida para {mac_desde_topic} | Worker {os.getpid()}", flush=True)

                mac_payload = data.get("mac")
                if mac_payload and mac_payload == mac_desde_topic:
                    await crear_dispositivo_o_artefacto(db, mac_desde_topic)
                    print(f"✅ Artefacto {mac_desde_topic} provisionado exitosamente.", flush=True)
                else:
                    print(f"⚠️ Anomalía en provisionamiento: MAC del payload no coincide.", flush=True)

            elif tipo_mensaje == "alerta":
                alerta_msg = data.get("alerta", "Alerta BMS crítica")
                ai_status = data.get("ai_status", 2)

                dispositivo = await emergencia_bms_shutdown(db, mac_desde_topic, alerta_msg, ai_status)
                if dispositivo:
                    await _broadcast_event(mac_desde_topic, "alerta", {
                        "alerta": alerta_msg,
                        "ai_status": ai_status,
                        "estado_reportado": False,
                    })
                    await enviar_push_a_duenos(
                        db, mac_desde_topic,
                        "🚨 Alerta Crítica BMS",
                        f"Apagado de emergencia por {alerta_msg}"
                    )
                    print(f"🚨 Alerta BMS {mac_desde_topic} -> {alerta_msg} (AI Status: {ai_status}) | Worker {os.getpid()}", flush=True)
                else:
                    print(f"⚠️ Alerta BMS ignorada — artefacto no encontrado: {mac_desde_topic}", flush=True)

    except Exception as e:
        print(f"❌ Error en Worker {os.getpid()}: {e}", flush=True)


def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode("utf-8")
    except Exception:
        return
    if _main_loop and _main_loop.is_running():
        asyncio.run_coroutine_threadsafe(procesar_payload(msg.topic, payload), _main_loop)


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

    client.loop_start()
    return client
