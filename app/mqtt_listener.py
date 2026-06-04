import os
import json
import asyncio
import paho.mqtt.client as mqtt
import paho.mqtt.publish as mqtt_publish
from app.database import AsyncSessionLocal
from app.crud import (
    crear_telemetria,
    crear_dispositivo_o_artefacto,
    obtener_dispositivo_por_mac,
    obtener_dispositivo_por_telemetria,
    actualizar_dispositivo,
    crear_alerta_si_necesario,
    resolver_alertas_por_tipo,
    crear_evento,
    romper_lease_por_seguridad,
    emergencia_bms_shutdown,
    enviar_push_a_duenos,
    procesar_cambio_conexion,
    procesar_reporte_estado,
)
from app.schemas import TelemetriaCreate
from app.config import settings

_main_loop = None


async def _broadcast_telemetry(mac: str, data: dict):
    # This is a no-op now because client_ws receives the telemetry directly via MQTT and broadcasts it.
    pass


async def _broadcast_event(mac: str, event_type: str, data: dict):
    try:
        # Publish event to Mosquitto so all workers' local clients broadcast it
        import paho.mqtt.publish as mqtt_publish
        import json
        topic = f"smartups/dispositivos/{mac}/broadcast/{event_type}"
        payload = json.dumps(data)
        mqtt_publish.single(
            topic, payload,
            hostname=settings.MQTT_HOST,
            port=settings.MQTT_PORT,
            auth={"username": settings.MQTT_USER, "password": settings.MQTT_PASS},
        )
    except Exception as e:
        print(f"❌ Error publishing WS event {event_type} for {mac}: {e}", flush=True)


def on_connect_db(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print(f"🔌 Worker {os.getpid()} de FastAPI conectado a Mosquitto (Shared Subscriptions)", flush=True)
        # Usar subscripciones compartidas ($share/backend/...) para balancear carga
        # y evitar que los 4 workers procesen el mismo mensaje y envíen 4 notificaciones
        client.subscribe("$share/backend/smartups/dispositivos/+/telemetria")
        client.subscribe("$share/backend/smartups/dispositivos/+/conexion")
        client.subscribe("$share/backend/smartups/dispositivos/+/reporte/estado")
        client.subscribe("$share/backend/smartups/dispositivos/+/reporte/limites")
        client.subscribe("$share/backend/smartups/dispositivos/+/provisionamiento")
        client.subscribe("$share/backend/smartups/dispositivos/+/alerta")
    else:
        print(f"❌ Error conectando Worker {os.getpid()} (DB). Código: {reason_code}", flush=True)


def on_connect_ws(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print(f"📡 Worker {os.getpid()} de FastAPI conectado a Mosquitto (Local WS Subscriptions)", flush=True)
        # Direct local subscriptions to receive messages across all workers for WebSocket broadcasting
        client.subscribe("smartups/dispositivos/+/telemetria")
        client.subscribe("smartups/dispositivos/+/conexion")
        client.subscribe("smartups/dispositivos/+/broadcast/+")
    else:
        print(f"❌ Error conectando Worker {os.getpid()} (WS). Código: {reason_code}", flush=True)


async def _verificar_alertas(db, artefacto, telemetria_in):
    limites = artefacto.limites
    if not limites:
        return

    violaciones_criticas = []  # Collect all emergency-level violations
    violacion_consumo = None   # Non-emergency consumption warning (separate)

    # --- Voltage ---
    if limites.limite_voltaje is not None and telemetria_in.voltaje > float(limites.limite_voltaje):
        alerta = await crear_alerta_si_necesario(
            db, artefacto.id, "sobretension",
            f"Voltaje {telemetria_in.voltaje:.2f}V excede límite {float(limites.limite_voltaje):.2f}V",
            "alta",
        )
        if alerta:
            violaciones_criticas.append(
                f"Voltaje ({telemetria_in.voltaje:.2f}V > {float(limites.limite_voltaje):.2f}V)"
            )
            await crear_evento(
                db, id_artefacto=artefacto.id,
                accion="safety_override",
                razon_disparo=f"Sobretensión {telemetria_in.voltaje:.2f}V rompe bloqueo de usuario",
            )
    else:
        await resolver_alertas_por_tipo(db, artefacto.id, "sobretension")

    # --- Current ---
    if limites.limite_corriente is not None and telemetria_in.corriente > float(limites.limite_corriente):
        alerta = await crear_alerta_si_necesario(
            db, artefacto.id, "sobrecorriente",
            f"Corriente {telemetria_in.corriente:.2f}A excede límite {float(limites.limite_corriente):.2f}A",
            "alta",
        )
        if alerta:
            violaciones_criticas.append(
                f"Corriente ({telemetria_in.corriente:.2f}A > {float(limites.limite_corriente):.2f}A)"
            )
            await crear_evento(
                db, id_artefacto=artefacto.id,
                accion="safety_override",
                razon_disparo=f"Sobrecorriente {telemetria_in.corriente:.2f}A rompe bloqueo de usuario",
            )
    else:
        await resolver_alertas_por_tipo(db, artefacto.id, "sobrecorriente")

    # --- Power ---
    if limites.limite_potencia is not None and telemetria_in.potencia > float(limites.limite_potencia):
        alerta = await crear_alerta_si_necesario(
            db, artefacto.id, "sobrepotencia",
            f"Potencia {telemetria_in.potencia:.2f}W excede límite {float(limites.limite_potencia):.2f}W",
            "alta",
        )
        if alerta:
            violaciones_criticas.append(
                f"Potencia ({telemetria_in.potencia:.2f}W > {float(limites.limite_potencia):.2f}W)"
            )
            await crear_evento(
                db, id_artefacto=artefacto.id,
                accion="safety_override",
                razon_disparo=f"Sobrepotencia {telemetria_in.potencia:.2f}W rompe bloqueo de usuario",
            )
    elif limites.limite_consumo_w > 0 and telemetria_in.potencia > float(limites.limite_consumo_w):
        alerta = await crear_alerta_si_necesario(
            db, artefacto.id, "sobrepotencia",
            f"Potencia {telemetria_in.potencia:.2f}W excede consumo límite {float(limites.limite_consumo_w):.2f}W",
            "media",
        )
        if alerta:
            violacion_consumo = (
                f"La potencia de {telemetria_in.potencia:.2f}W excede el consumo límite "
                f"configurado de {float(limites.limite_consumo_w):.2f}W."
            )
    else:
        await resolver_alertas_por_tipo(db, artefacto.id, "sobrepotencia")

    # --- Single consolidated push notification + lease break + device shutdown ---
    if violaciones_criticas:
        # Break user lease for safety
        artefacto.override_activo = False
        artefacto.vencimiento_lease = None

        # Turn off the device via DB + MQTT command
        artefacto.estado_deseado = False
        artefacto.estado_reportado = False
        
        # Disable active schedule automation for protection
        automation_disabled = False
        if artefacto.horario and artefacto.horario.automatizacion_activa:
            artefacto.horario.automatizacion_activa = False
            automation_disabled = True

        await db.commit()

        # Publish MQTT off command to physically turn off the device
        try:
            mqtt_publish.single(
                f"smartups/dispositivos/{artefacto.mac}/comando/estado",
                json.dumps({"encendido": False}),
                hostname=settings.MQTT_HOST,
                port=settings.MQTT_PORT,
                auth={"username": settings.MQTT_USER, "password": settings.MQTT_PASS},
            )
        except Exception as e:
            print(f"❌ Error publicando apagado de emergencia por límites para {artefacto.mac}: {e}", flush=True)

        auto_suffix = " La automatización por horario fue desactivada por protección." if automation_disabled else ""
        await enviar_push_a_duenos(
            db, artefacto.mac,
            "⚡ Límite de Consumo Excedido",
            f"El dispositivo ha sido apagado de emergencia debido a: {', '.join(violaciones_criticas)}.{auto_suffix}"
        )
    elif violacion_consumo:
        await enviar_push_a_duenos(
            db, artefacto.mac,
            "⚠️ Alerta de Consumo Alto",
            violacion_consumo
        )


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

                    # Broadcast is handled by the local WS client
                    pass
                else:
                    print(f"⚠️ Worker {os.getpid()}: Artefacto no provisionado ignorado ({mac_desde_topic}).", flush=True)

            elif tipo_mensaje == "conexion":
                estado_actual = data.get("is_online")

                if estado_actual is not None:
                    estado_bool = bool(estado_actual)
                    ok, cambio = await procesar_cambio_conexion(db, mac_desde_topic, online=estado_bool)
                    if ok:
                        if cambio:
                            # Broadcast is handled by the local WS client
                            pass
                        estado_str = "Online 🟢" if estado_bool else "Offline 🔴"
                        print(f"🔄 Estado de {mac_desde_topic} -> {estado_str} | Worker {os.getpid()}", flush=True)

            elif tipo_mensaje == "reporte":
                subtipo = partes_topic[4] if len(partes_topic) > 4 else ""

                if subtipo == "estado":
                    encendido = data.get("encendido")
                    if encendido is not None:
                        ok, cambio = await procesar_reporte_estado(db, mac_desde_topic, encendido=bool(encendido))
                        if ok:
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

                res = await emergencia_bms_shutdown(db, mac_desde_topic, alerta_msg, ai_status)
                if res:
                    dispositivo, alerta_creada, automation_disabled = res
                    await _broadcast_event(mac_desde_topic, "alerta", {
                        "alerta": alerta_msg,
                        "ai_status": ai_status,
                        "estado_reportado": False,
                        "automation_disabled": automation_disabled,
                    })
                    if alerta_creada:
                        auto_suffix = " La automatización por horario fue desactivada por protección." if automation_disabled else ""
                        await enviar_push_a_duenos(
                            db, mac_desde_topic,
                            "🚨 Alerta Crítica BMS",
                            f"Apagado de emergencia por {alerta_msg}.{auto_suffix}"
                        )
                    print(f"🚨 Alerta BMS {mac_desde_topic} -> {alerta_msg} (AI Status: {ai_status}) | Worker {os.getpid()}", flush=True)
                else:
                    print(f"⚠️ Alerta BMS ignorada — artefacto no encontrado: {mac_desde_topic}", flush=True)

    except Exception as e:
        print(f"❌ Error en Worker {os.getpid()}: {e}", flush=True)


_client_db = None
_client_ws = None


def on_message_db(client, userdata, msg):
    try:
        payload = msg.payload.decode("utf-8")
    except Exception:
        return
    if _main_loop and _main_loop.is_running():
        asyncio.run_coroutine_threadsafe(procesar_payload(msg.topic, payload), _main_loop)


def on_message_ws(client, userdata, msg):
    try:
        payload = msg.payload.decode("utf-8")
        data = json.loads(payload)
    except Exception:
        return

    try:
        partes_topic = msg.topic.split("/")
        if len(partes_topic) < 4:
            return

        mac_desde_topic = partes_topic[2]
        tipo_mensaje = partes_topic[3]

        if _main_loop and _main_loop.is_running():
            from app.ws_manager import ws_manager
            if tipo_mensaje == "telemetria":
                asyncio.run_coroutine_threadsafe(
                    ws_manager.broadcast_telemetry(mac_desde_topic, data), _main_loop
                )
            elif tipo_mensaje == "conexion":
                estado_actual = data.get("is_online")
                if estado_actual is not None:
                    asyncio.run_coroutine_threadsafe(
                        ws_manager.broadcast_event(mac_desde_topic, "conexion", {"is_online": bool(estado_actual)}), _main_loop
                    )
            elif tipo_mensaje == "broadcast":
                event_type = partes_topic[4] if len(partes_topic) > 4 else "event"
                asyncio.run_coroutine_threadsafe(
                    ws_manager.broadcast_event(mac_desde_topic, event_type, data), _main_loop
                )
    except Exception as e:
        print(f"❌ Error WS callback en Worker {os.getpid()}: {e}", flush=True)


def iniciar_oyente_mqtt():
    global _main_loop, _client_db, _client_ws
    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        print("⚠️ Advertencia: No se detectó un event loop de asyncio en ejecución.", flush=True)
        return None

    pid_actual = os.getpid()

    # 1. DB & Alerts Listener (Shared Subscription)
    _client_db = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"FastAPI_DB_{pid_actual}",
    )
    _client_db.username_pw_set(settings.MQTT_USER, settings.MQTT_PASS)
    _client_db.on_connect = on_connect_db
    _client_db.on_message = on_message_db
    _client_db.connect(settings.MQTT_HOST, settings.MQTT_PORT, 60)
    _client_db.loop_start()

    # 2. Local WS Broadcaster (Non-shared Subscription)
    _client_ws = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"FastAPI_WS_{pid_actual}",
    )
    _client_ws.username_pw_set(settings.MQTT_USER, settings.MQTT_PASS)
    _client_ws.on_connect = on_connect_ws
    _client_ws.on_message = on_message_ws
    _client_ws.connect(settings.MQTT_HOST, settings.MQTT_PORT, 60)
    _client_ws.loop_start()

    return _client_db


def detener_oyente_mqtt():
    global _client_db, _client_ws
    if _client_db:
        try:
            _client_db.loop_stop()
            _client_db.disconnect()
        except Exception:
            pass
        _client_db = None
    if _client_ws:
        try:
            _client_ws.loop_stop()
            _client_ws.disconnect()
        except Exception:
            pass
        _client_ws = None
