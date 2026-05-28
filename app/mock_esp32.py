import time
import json
import random
import math
import torch
import torch.nn as nn
import paho.mqtt.client as mqtt

from config import settings

MAC_ESP32 = "00:1B:44:11:3A:B7"

estado_rele_encendido = True
tiempo_operacion_s = 0

# --- TRACKERS DE LA MÁQUINA DE ESTADOS Y PLANIFICADOR ---
estado_red = "NORMAL"
ciclos_en_bajon = 0
proximo_bajon_s = 180  # El primer bajón ocurrirá exactamente a los 3 minutos (180s)

# --- 1. DEFINICIÓN DE LA IA ---
class SmartSaverMLP(nn.Module):
    def __init__(self):
        super(SmartSaverMLP, self).__init__()
        self.fc1 = nn.Linear(4, 16)
        self.relu1 = nn.ReLU()
        self.fc2 = nn.Linear(16, 16)
        self.relu2 = nn.ReLU()
        self.out = nn.Linear(16, 3)
        
    def forward(self, x):
        x = self.relu1(self.fc1(x))
        x = self.relu2(self.fc2(x))
        return self.out(x)

# --- 2. INICIALIZACIÓN GLOBAL ---
SCALER_MEAN = [1.19815335e+02, 1.93682696e-01, 2.30792778e+01, 4.25903146e+04]
SCALER_VAR  = [1.55115181e-01, 1.30665406e-01, 1.79889282e+03, 6.18074710e+08]

print("[INIT] Cargando el Cerebro IA...")
model = SmartSaverMLP()
model.load_state_dict(torch.load('smartsaver_mlp_weights.pth', map_location=torch.device('cpu')))
model.eval()

# --- 3. CALLBACKS MQTT ---
def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code != 0:
        print(f"[ERROR] Conexion rechazada, codigo: {reason_code}")
        return
    print(f"[OK] ESP32 simulador conectado a {settings.MQTT_HOST}:{settings.MQTT_PORT}")
    client.subscribe(f"smartups/dispositivos/{MAC_ESP32}/comando/#")
    
    topic_conexion = f"smartups/dispositivos/{MAC_ESP32}/conexion"
    client.publish(topic_conexion, json.dumps({"is_online": True}), qos=1, retain=True)

def on_message(client, userdata, msg):
    global estado_rele_encendido, estado_red, ciclos_en_bajon, proximo_bajon_s, tiempo_operacion_s
    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except json.JSONDecodeError:
        return

    if "comando/estado" in topic:
        nuevo_estado = payload.get("encendido")
        if nuevo_estado is not None:
            estado_rele_encendido = bool(nuevo_estado)
            print(f"\n[ACT] Comando Recibido. Rele -> {'ON' if estado_rele_encendido else 'OFF'}")
            
            if estado_rele_encendido:
                print("[REINICIO] Sistema rearmado. Retomando monitoreo de la red...")
                # Resetear la máquina de estados y dar 3 minutos de gracia antes del próximo bajón
                estado_red = "NORMAL"
                ciclos_en_bajon = 0
                proximo_bajon_s = tiempo_operacion_s + 180 
                
            client.publish(f"smartups/dispositivos/{MAC_ESP32}/reporte/estado", 
                           json.dumps({"encendido": estado_rele_encendido}), qos=1)

# --- 4. MOTOR PRINCIPAL: FÍSICA TRANSITORIA DE LA RED ---
def publish_telemetry(client):
    global estado_rele_encendido, tiempo_operacion_s, estado_red, ciclos_en_bajon, proximo_bajon_s

    if not estado_rele_encendido:
        return

    tiempo_operacion_s += 5

    # 1. Carga de Potencia Constante: El router (15W)
    potencia = random.uniform(14.0, 16.0) 

    # 2. MÁQUINA DE ESTADOS: Planificador de eventos eléctricos
    if estado_red == "NORMAL":
        # Verificamos si ya es hora del siguiente bajón
        if tiempo_operacion_s >= proximo_bajon_s: 
            estado_red = "BAJON"
            ciclos_en_bajon = random.randint(2, 4) # El bajón dura entre 10s y 20s
            voltaje_base = random.uniform(60.0, 90.0) 
            print(f"\n📉 [GRID] ¡CAÍDA DE TENSIÓN! Evento programado alcanzado (Uptime: {tiempo_operacion_s}s)")
            
            # Programar el próximo bajón para dentro de 2.5 a 3.5 minutos (150s a 210s)
            proximo_bajon_s = tiempo_operacion_s + random.randint(150, 210)
        else:
            voltaje_base = 120.0 - (potencia * 0.008)
            
    elif estado_red == "BAJON":
        ciclos_en_bajon -= 1
        voltaje_base = random.uniform(60.0, 90.0)
        
        if ciclos_en_bajon <= 0:
            estado_red = "PICO" # Siguiente ciclo será el latigazo de recuperación
            print(f"📉 [GRID] Subtensión severa. La red intenta reconectar...")
        else:
            print(f"📉 [GRID] Subtensión severa. (Quedan {ciclos_en_bajon * 5} segundos)")
            
    elif estado_red == "PICO":
        # Latigazo inductivo (Sobretensión transitoria)
        voltaje_base = random.uniform(250.0, 320.0)
        print("\n⚡ [GRID] ¡LATIGAZO INDUCTIVO! Pico de tensión destructivo ingresando al sistema.")
        estado_red = "NORMAL" # Regresa a la normalidad en el siguiente ciclo

    # 3. Aplicación de las leyes físicas de carga
    ruido = random.gauss(0, 0.2) 
    voltaje = round(voltaje_base + ruido, 2)
    corriente = round(potencia / voltaje, 3) 

    # 4. ESCALADO DE DATOS (StandardScaler)
    tiempo_ia = 86400 

    v_scaled = (voltaje - SCALER_MEAN[0]) / math.sqrt(SCALER_VAR[0])
    i_scaled = (corriente - SCALER_MEAN[1]) / math.sqrt(SCALER_VAR[1])
    p_scaled = (potencia - SCALER_MEAN[2]) / math.sqrt(SCALER_VAR[2])
    t_scaled = (tiempo_ia - SCALER_MEAN[3]) / math.sqrt(SCALER_VAR[3])

    # 5. INFERENCIA DE LA RED NEURONAL CON SOFTMAX
    with torch.no_grad():
        input_tensor = torch.tensor([v_scaled, i_scaled, p_scaled, t_scaled], dtype=torch.float32)
        logits = model(input_tensor)
        
        probabilidades = torch.softmax(logits, dim=0)
        prob_safe = probabilidades[0].item()
        prob_risky = probabilidades[1].item()
        prob_critical = probabilidades[2].item()
        
        ai_class = torch.argmax(logits).item() 

    # Se reincorporó la probabilidad de RISKY en la consola
    print(f"   -> {voltaje}V | {corriente}A | {potencia:.2f}W | IA (SAFE: {prob_safe:.0%} | RISKY: {prob_risky:.0%} | CRIT: {prob_critical:.0%})")

    # 6. LÓGICA DE PROTECCIÓN DE HARDWARE
    if ai_class == 2 and prob_critical > 0.85:
        print("\n[!!!] PROTECCIÓN ACTIVADA: ANOMALÍA CRÍTICA DETECTADA (>85%). APAGANDO RELÉ [!!!]")
        print("Esperando comando MQTT de rearme en: smartups/dispositivos/.../comando/estado\n")
        estado_rele_encendido = False
        topic_alerta = f"smartups/dispositivos/{MAC_ESP32}/alerta"
        client.publish(topic_alerta, json.dumps({"alerta": "COLAPSO_ELECTRICO", "corte_automatico": True}), qos=2)
        client.publish(f"smartups/dispositivos/{MAC_ESP32}/reporte/estado", json.dumps({"encendido": False}), qos=1)
        return 

    # 7. TRANSMISIÓN DE TELEMETRÍA ESTÁNDAR
    telemetria = {
        "mac_dispositivo": MAC_ESP32,
        "voltaje": voltaje,
        "corriente": corriente,
        "potencia": round(potencia, 2),
        "tiempo_operacion_s": tiempo_operacion_s, 
        "ai_estado": ai_class,
        "confianza_critica": round(prob_critical, 2)
    }
    client.publish(f"smartups/dispositivos/{MAC_ESP32}/telemetria", json.dumps(telemetria), qos=1)

def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="esp32_ai_sim")
    client.on_connect = on_connect
    client.on_message = on_message
    client.username_pw_set(username=settings.MQTT_USER, password=settings.MQTT_PASS)

    print(f"[INIT] Conectando a {settings.MQTT_HOST} ...")
    client.connect(settings.MQTT_HOST, settings.MQTT_PORT, 60)
    client.loop_start()

    try:
        while True:
            publish_telemetry(client)
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        client.publish(f"smartups/dispositivos/{MAC_ESP32}/conexion", json.dumps({"is_online": False}), qos=1, retain=True)
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()
