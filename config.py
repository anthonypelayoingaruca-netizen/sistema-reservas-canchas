import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG = {
    "nombre_negocio": "Campo Deportivo - 2 Canchas",
    "canchas": ["Cancha 1", "Cancha 2"],
    "precio_dia": 60,
    "precio_noche": 80,
    "DB_PATH": os.path.join(BASE_DIR, "reservas.db")
}