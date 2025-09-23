import asyncio
import os
import time
from bleak import BleakScanner
from collections import deque

# --- Configuración ---
# Tiempo en segundos para que una baliza desaparezca si no se detecta
BEACON_TIMEOUT = 10
# Número de lecturas de RSSI a promediar para suavizar la señal
RSSI_SAMPLES_COUNT = 10
# Umbral (en dBm) para considerar un cambio de estado (movimiento)
TREND_THRESHOLD = 1.5

# Diccionario para almacenar los datos de cada baliza
detected_beacons = {}

def clear_screen():
    """Limpia la pantalla de la terminal."""
    os.system('cls' if os.name == 'nt' else 'clear')

async def main():
    def detection_callback(device, advertising_data):
        """Esta función se llama con cada detección y solo recolecta datos."""
        manufacturer_data = advertising_data.manufacturer_data
        
        # 1. Primero, verificamos si la clave de Apple existe.
        if 0x004c in manufacturer_data:
            # 2. Si existe, AHORA SÍ creamos la variable 'beacon_data'.
            beacon_data = manufacturer_data[0x004c]
            
            # 3. Ahora que 'beacon_data' existe, ya podemos usarla en la siguiente condición.
            if beacon_data[0:2] == b'\x02\x15':
                beacon_data = manufacturer_data[0x004c]
                device_key = device.address


                # Si es la primera vez que vemos este dispositivo, creamos su entrada
                if device_key not in detected_beacons:
                    detected_beacons[device_key] = {
                        'uuid': beacon_data[2:18].hex(),
                        'major': int.from_bytes(beacon_data[18:20], 'big'),
                        'minor': int.from_bytes(beacon_data[20:22], 'big'),
                        'readings': deque(maxlen=RSSI_SAMPLES_COUNT),
                        'avg_rssi': None,
                        'prev_avg_rssi': None,
                        'trend': 'Calculando...'
                    }
                
                # Añadimos la nueva lectura de RSSI y actualizamos la hora
                detected_beacons[device_key]['readings'].append(advertising_data.rssi)
                detected_beacons[device_key]['last_seen'] = time.time()

    # Iniciar el escaneo en segundo plano
    scanner = BleakScanner(detection_callback)
    await scanner.start()
    
    try:
        # Bucle principal para procesar datos y dibujar la pantalla
        while True:
            current_time = time.time()
            
            # Procesar cada baliza detectada
            for key, data in list(detected_beacons.items()):
                # Eliminar balizas que no se han visto recientemente
                if current_time - data['last_seen'] > BEACON_TIMEOUT:
                    del detected_beacons[key]
                    continue # Pasar a la siguiente iteración

                # --- Lógica de Promedio y Tendencia ---
                readings = data['readings']
                if len(readings) > 0:
                    # 1. Suavizar la señal (Averaging)
                    new_avg = sum(readings) / len(readings)
                    
                    # Guardamos el promedio anterior antes de actualizarlo
                    if data['avg_rssi'] is not None:
                        data['prev_avg_rssi'] = data['avg_rssi']
                    data['avg_rssi'] = new_avg

                    # 2. Enfocarse en el cambio (Delta)
                    if data['prev_avg_rssi'] is not None:
                        delta = data['avg_rssi'] - data['prev_avg_rssi']
                        
                        if delta > TREND_THRESHOLD:
                            data['trend'] = 'Acercándose ⬆️'
                        elif delta < -TREND_THRESHOLD:
                            data['trend'] = 'Alejándose ⬇️'
                        else:
                            data['trend'] = 'Estable ⏸️'
            
            # --- Lógica para Dibujar la Pantalla ---
            clear_screen()
            print("📡 Escáner de iBeacons con Suavizado de Señal (Ctrl+C para salir)")
            print("=" * 70)
            
            if not detected_beacons:
                print("\nBuscando balizas...")
            else:
                for key, data in detected_beacons.items():
                    avg_rssi_str = f"{data['avg_rssi']:.1f}" if data['avg_rssi'] is not None else "N/A"
                    print(f"Dispositivo: {key} | RSSI Promedio: {avg_rssi_str} dBm | Estado: {data['trend']}")
                    print(f"  -> UUID: {data['uuid']}")
                    print(f"  -> Major: {data['major']}, Minor: {data['minor']}\n")

            await asyncio.sleep(1.0) # Refrescar la pantalla cada segundo

    except KeyboardInterrupt:
        print("\n🛑 Deteniendo el escaneo...")
    finally:
        await scanner.stop()
        print("✅ Script finalizado.")

if __name__ == "__main__":
    asyncio.run(main())