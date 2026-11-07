import sqlite3
from config import CONFIG
from werkzeug.security import generate_password_hash
import os

# DB principal (solo para usuarios e historial)
DB_PATH = CONFIG.get('DB_PATH', 'reservas.db')

# DBs independientes por sede
DB_COMAS = 'reservas_comas.db'
DB_JICAMARCA = 'reservas_jicamarca.db'

def get_conn(sede=None):
    """
    Si pides sede COMAS o JICAMARCA, te da su DB independiente.
    Si no pides sede, te da la DB de usuarios (DB_PATH)
    """
    if sede == 'COMAS':
        conn = sqlite3.connect(DB_COMAS, timeout=10)
    elif sede == 'JICAMARCA':
        conn = sqlite3.connect(DB_JICAMARCA, timeout=10)
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
    
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = sqlite3.Row
    return conn

# Alias claros para app.py
def get_reservas_conn(sede):
    if sede not in ('COMAS','JICAMARCA'):
        raise ValueError("Sede debe ser COMAS o JICAMARCA")
    return get_conn(sede)

def get_users_conn():
    return get_conn() # central usuarios

def init_db():
    # 1. DB DE USUARIOS (central)
    conn = get_conn()
    c = conn.cursor()
    
    c.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password_hash TEXT,
        rol TEXT,
        sede TEXT DEFAULT 'TODAS',
        creado TEXT,
        debe_cambiar_password INTEGER DEFAULT 1
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS historial_reservas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reserva_id INTEGER,
        accion TEXT,
        usuario TEXT,
        rol TEXT,
        sede TEXT,
        detalle TEXT,
        fecha_hora TEXT
    )""")

    # Parches por si la DB ya existía de antes
    try: c.execute("ALTER TABLE usuarios ADD COLUMN sede TEXT DEFAULT 'TODAS'")
    except: pass
    try: c.execute("ALTER TABLE usuarios ADD COLUMN debe_cambiar_password INTEGER DEFAULT 1")
    except: pass
    try: c.execute("ALTER TABLE usuarios ADD COLUMN creado TEXT")
    except: pass
    try: c.execute("ALTER TABLE historial_reservas ADD COLUMN sede TEXT")
    except: pass

    # Dueño por defecto: NO debe cambiar pass
    if not c.execute("SELECT * FROM usuarios WHERE username='dueno'").fetchone():
        c.execute("INSERT INTO usuarios (username,password_hash,rol,sede,creado,debe_cambiar_password) VALUES (?,?,?,?,?,?)",
                  ('dueno', generate_password_hash('dueno123'), 'DUENO', 'TODAS', '2026-01-01', 0))
        print("👑 Usuario dueno / dueno123 creado (no necesita cambiar)")

    # Para usuarios viejos que tenian 0, fuerza a 1 excepto dueno
    c.execute("UPDATE usuarios SET debe_cambiar_password=1 WHERE username!='dueno' AND (debe_cambiar_password IS NULL OR debe_cambiar_password=0)")

    conn.commit()
    conn.close()

    # 2. DBs DE RESERVAS (independientes)
    for sede, db_file in [('COMAS', DB_COMAS), ('JICAMARCA', DB_JICAMARCA)]:
        conn = sqlite3.connect(db_file, timeout=10)
        conn.execute("PRAGMA busy_timeout = 5000")
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS reservas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT,
            cancha TEXT,
            fecha TEXT,
            hora TEXT,
            hora_inicio TEXT,
            hora_fin TEXT,
            duracion REAL,
            telefono TEXT,
            monto_total INTEGER,
            adelanto INTEGER,
            estado TEXT,
            boleta_adelanto TEXT,
            boleta_final TEXT,
            creado TEXT,
            creado_por TEXT,
            actualizado_por TEXT,
            actualizado_en TEXT
        )""")
        for col in ["creado_por","actualizado_por","actualizado_en"]:
            try: c.execute(f"ALTER TABLE reservas ADD COLUMN {col} TEXT")
            except: pass
        conn.commit()
        conn.close()

    print(f"✅ DBs listas:")
    print(f"   - Usuarios: {DB_PATH} (debe_cambiar_password=1 por defecto)")
    print(f"   - Comas: {DB_COMAS} (independiente)")
    print(f"   - Jicamarca: {DB_JICAMARCA} (independiente)")

init_db()