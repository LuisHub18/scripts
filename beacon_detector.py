import flet as ft
import time
import threading
import statistics
from collections import deque
from bleak import BleakScanner
import asyncio
import os
import psycopg2
from dotenv import load_dotenv
import math


load_dotenv()

# --- Configuración del Escáner y la App ---
BEACON_TIMEOUT = 15
RSSI_SAMPLES_COUNT = 10
CALIBRATION_DURATION = 10  # Segundos

# Objeto de estado compartido entre hilos
APP_STATE = {
    "mode": "IDLE",  # Estados: IDLE, CALIBRATING, MONITORING
    "calibration_end_time": 0,
    "detected_beacons": {},
    "perimeter_rssi_levels": [] # Lista para guardar los 3 niveles de RSSI del perímetro
}

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "client_encoding": os.getenv("DB_CLIENT_ENCODING", "utf8")
}

def log_zone_change_event(mac_address, old_zone, new_zone, logger):
    """Registra un cambio de zona en la tabla de eventos."""
    logger(f"DB: Registrando evento de zona para {mac_address}...")
    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        with conn.cursor() as cur:
            mensaje = f"Alerta: Baliza {mac_address} cambió de zona {old_zone} a {new_zone}."
            cur.execute("INSERT INTO eventos (mensaje) VALUES (%s)", (mensaje,))
            conn.commit()
            logger(f"DB: OK - Evento registrado para {mac_address}.")
    except Exception as e:
        logger(f"DB: ERROR al registrar evento para {mac_address}: {e}")
    finally:
        if conn:
            conn.close()

def ble_scanner_thread():
    """Hilo de escaneo que solo se encarga de recolectar datos."""
    
    def detection_callback(device, advertising_data):
        manufacturer_data = advertising_data.manufacturer_data
        if 0x004c in manufacturer_data:
            beacon_data = manufacturer_data[0x004c]
            if beacon_data[0:2] == b'\x02\x15':
                device_key = device.address
                
                with threading.Lock():
                    if device_key not in APP_STATE["detected_beacons"]:
                        APP_STATE["detected_beacons"][device_key] = {
                            'uuid': beacon_data[2:18].hex(),
                            'name': device.name if device.name else "Desconocido",
                            'readings': deque(maxlen=RSSI_SAMPLES_COUNT),
                            'avg_rssi': -100,
                            'home_rssi': None,
                            'status': 'NUEVO'
                        }
                    
                    APP_STATE["detected_beacons"][device_key]['readings'].append(advertising_data.rssi)
                    APP_STATE["detected_beacons"][device_key]['last_seen'] = time.time()

    async def scan_loop():
        scanner = BleakScanner(detection_callback)
        await scanner.start()
        while True:
            await asyncio.sleep(0.2)

    asyncio.run(scan_loop())

def main(page: ft.Page):
    page.title = "Mapa de Perímetro Dinámico BLE"
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.bgcolor = "#1f262f"
    
    logs_view = ft.Column(controls=[], scroll=ft.ScrollMode.ADAPTIVE, height=150, spacing=5)

    def add_log_message(message: str):
        print(f"[APP LOG] {message}")
        try:
            timestamp = time.strftime('%H:%M:%S')
            log_entry = ft.Text(f"[{timestamp}] {message}", size=11, font_family="monospace")
            logs_view.controls.append(log_entry)
            if len(logs_view.controls) > 50:
                logs_view.controls.pop(0)
            page.update()
        except Exception as e:
            print(f"[APP LOG] CRITICAL: Falla al actualizar la UI del log. Error: {e}")

    def start_calibration(e):
        APP_STATE["mode"] = "CALIBRATING"
        APP_STATE["calibration_end_time"] = time.time() + CALIBRATION_DURATION
        APP_STATE["perimeter_rssi_levels"] = []
        for data in APP_STATE["detected_beacons"].values():
            data['home_rssi'] = None
            data['status'] = 'CALIBRANDO'
        calibrate_button.disabled = True
        add_log_message("Iniciando calibración...")
        
    def map_rssi_to_distance(rssi, map_radius):
        rssi_min, rssi_max = -95, -35
        normalized_rssi = max(0, min(1, (rssi - rssi_min) / (rssi_max - rssi_min)))
        return (1 - normalized_rssi) * map_radius

    txt_status = ft.Text("Presiona 'Iniciar Calibración' para definir el perímetro.", size=16, weight=ft.FontWeight.BOLD)
    calibrate_button = ft.ElevatedButton("Iniciar Calibración", on_click=start_calibration, icon=ft.Icons.SETTINGS_INPUT_COMPONENT)
    
    MAP_SIZE = 400
    map_stack = ft.Stack(
        width=MAP_SIZE,
        height=MAP_SIZE,
        controls=[
            ft.Container(width=MAP_SIZE, height=MAP_SIZE, border=ft.border.all(1, ft.Colors.BLUE_GREY_900), border_radius=ft.border_radius.all(MAP_SIZE/2)),
            ft.Icon(ft.Icons.MY_LOCATION, color=ft.Colors.CYAN, size=30)
        ]
    )

    beacons_list_view = ft.Column(controls=[], spacing=5, horizontal_alignment=ft.CrossAxisAlignment.CENTER)

    page.add(
        ft.Column(
            [
                ft.Text("📡 Mapa de Proximidad de Balizas", size=24, weight=ft.FontWeight.BOLD),
                txt_status,
                calibrate_button,
                map_stack,
                ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                ft.Text("Balizas Detectadas:", size=16, weight=ft.FontWeight.BOLD, text_align=ft.TextAlign.CENTER),
                beacons_list_view,
                ft.Divider(height=20, color=ft.Colors.BLUE_GREY_900),
                ft.Text("Logs del Sistema:", size=16, weight=ft.FontWeight.BOLD),
                ft.Container(
                    content=logs_view,
                    border=ft.border.all(1, ft.Colors.BLUE_GREY_900),
                    border_radius=5,
                    padding=10
                )
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=15
        )
    )

    def ui_update_loop():
        while True:
            current_time = time.time()
            
            beacons_to_process = list(APP_STATE["detected_beacons"].items())

            for key, data in beacons_to_process:
                if current_time - data.get('last_seen', 0) > BEACON_TIMEOUT:
                    if key in APP_STATE["detected_beacons"]:
                        del APP_STATE["detected_beacons"][key]
                    continue
                
                if data['readings']:
                    data['avg_rssi'] = statistics.mean(data['readings'])

            if APP_STATE["mode"] == "CALIBRATING":
                remaining_time = APP_STATE["calibration_end_time"] - current_time
                if remaining_time > 0:
                    txt_status.value = f"Calibrando... Definiendo perímetro en {remaining_time:.0f}s"
                else:
                    APP_STATE["mode"] = "MONITORING"
                    txt_status.value = "Monitoreando. Perímetros definidos."
                    calibrate_button.disabled = False
                    add_log_message("Calibración finalizada. Definiendo perímetros.")
                    
                    for data in APP_STATE["detected_beacons"].values():
                        if data['avg_rssi'] is not None:
                            data['home_rssi'] = data['avg_rssi']

                    home_rssis = [d['home_rssi'] for d in APP_STATE["detected_beacons"].values() if d.get('home_rssi') is not None]
                    
                    p_inner, p_mid, p_outer = 0, 0, 0

                    if home_rssis:
                        weakest_rssi = min(home_rssis)
                        p_inner = weakest_rssi - 5
                        p_mid = p_inner - 15 
                        p_outer = p_inner - 30
                        add_log_message(f"Perímetros RSSI: Z1 > {p_inner:.1f}, Z2 > {p_mid:.1f}, Z3 > {p_outer:.1f}")

                    p_inner = min(-35, p_inner)
                    APP_STATE["perimeter_rssi_levels"] = sorted([p_inner, p_mid, p_outer], reverse=True)

            elif APP_STATE["mode"] == "MONITORING":
                if APP_STATE["perimeter_rssi_levels"]:
                    p_inner, p_mid, p_outer = APP_STATE["perimeter_rssi_levels"]
                    for key, data in APP_STATE["detected_beacons"].items():
                        if data.get('avg_rssi') is not None:
                            rssi = data['avg_rssi']
                            old_status = data.get('status', 'NUEVO')
                            new_status = old_status

                            if rssi >= p_inner:
                                new_status = 'ZONA 1'
                            elif p_mid <= rssi < p_inner:
                                new_status = 'ZONA 2'
                            elif p_outer <= rssi < p_mid:
                                new_status = 'ZONA 3'
                            else:
                                new_status = 'FUERA'
                            
                            if new_status != old_status:
                                data['status'] = new_status
                                add_log_message(f"Baliza {key} cambió de {old_status} a {new_status}.")
                                if new_status == 'FUERA' or old_status == 'FUERA':
                                    log_zone_change_event(key, old_status, new_status, add_log_message)
            
            map_stack.controls = map_stack.controls[:2]
            
            UNIFIED_MAP_RADIUS = MAP_SIZE / 2 - 10

            if APP_STATE["perimeter_rssi_levels"]:
                for rssi_level in APP_STATE["perimeter_rssi_levels"]:
                    distance = map_rssi_to_distance(rssi_level, UNIFIED_MAP_RADIUS)
                    size = distance * 2
                    pos = (MAP_SIZE / 2) - distance
                    perimeter_circle = ft.Container(
                        width=size, height=size, left=pos, top=pos,
                        border=ft.border.all(1, ft.Colors.BLUE_GREY_700),
                        border_radius=ft.border_radius.all(distance)
                    )
                    map_stack.controls.append(perimeter_circle)

            status_color_map = {
                "NUEVO": ft.Colors.GREY, "CALIBRANDO": ft.Colors.BLUE,
                "ZONA 1": ft.Colors.GREEN_ACCENT, "ZONA 2": ft.Colors.YELLOW_ACCENT,
                "ZONA 3": ft.Colors.ORANGE_ACCENT, "FUERA": ft.Colors.RED_ACCENT
            }

            for key, data in APP_STATE["detected_beacons"].items():
                if 'avg_rssi' not in data: continue
                
                status_color = status_color_map.get(data['status'], ft.Colors.GREY)
                
                distance = map_rssi_to_distance(data['avg_rssi'], UNIFIED_MAP_RADIUS)
                angle = (hash(key) % 360) * (math.pi / 180) 
                
                x = (MAP_SIZE / 2) + distance * math.cos(angle)
                y = (MAP_SIZE / 2) + distance * math.sin(angle)

                beacon_dot = ft.Container(
                    width=20, height=20, bgcolor=status_color, border_radius=10,
                    left=x-10, top=y-10,
                    tooltip=f"Nombre: {data.get('name', 'N/A')}\nMAC: {key}\nRSSI: {data['avg_rssi']:.1f} dBm\nStatus: {data['status']}"
                )
                map_stack.controls.append(beacon_dot)

            beacons_list_view.controls.clear()
            sorted_beacons = sorted(APP_STATE["detected_beacons"].items())

            for key, data in sorted_beacons:
                if 'avg_rssi' in data:
                    text_color = status_color_map.get(data['status'], ft.Colors.GREY)
                    beacons_list_view.controls.append(
                        ft.Text(
                            f"{data.get('name', 'Desconocido')} ({key}) | RSSI: {data['avg_rssi']:.1f} dBm ({data['status']})",
                            color=text_color, weight=ft.FontWeight.W_500
                        )
                    )
            
            page.update()
            time.sleep(0.5)

    threading.Thread(target=ble_scanner_thread, daemon=True).start()
    threading.Thread(target=ui_update_loop, daemon=True).start()

ft.app(target=main)