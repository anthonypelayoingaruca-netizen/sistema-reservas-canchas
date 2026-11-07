from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from config import CONFIG
from database import init_db, get_conn, get_reservas_conn

app = Flask(__name__)
app.secret_key = 'clave-super-secreta-cambia-esto-12345'
init_db()

# --- AUDITORIA ---
def registrar_historial(reserva_id, accion, detalle=""):
    try:
        conn = get_conn() # usuarios.db - donde está el historial
        conn.execute("INSERT INTO historial_reservas (reserva_id, accion, usuario, rol, sede, detalle, fecha_hora) VALUES (?,?,?,?,?,?,?)",
                     (reserva_id, accion, session.get('username','-'), session.get('rol','-'), session.get('sede','-'), detalle, datetime.now().strftime("%d/%m/%Y %H:%M:%S")))
        conn.commit()
        conn.close()
    except Exception as e:
        print("Error historial:", e)

# --- DECORADORES ---
def login_requerido(f):
    @wraps(f)
    def decor(*args,**kwargs):
        if 'user_id' not in session:
            next_url = request.full_path if request.query_string else request.path
            return redirect(url_for('login') + f"?next={next_url}")
        # 🔒 BLOQUEO OBLIGATORIO DE CAMBIO
        if session.get('debe_cambiar'):
            # permitir solo estas rutas mientras no cambie
            if request.path not in ['/cambiar-password', '/api/cambiar-password', '/logout']:
                if request.endpoint not in ['cambiar_password', 'api_cambiar_password', 'logout']:
                    return redirect(url_for('cambiar_password'))
        return f(*args,**kwargs)
    return decor

def rol_requerido(roles):
    if isinstance(roles, str):
        roles = [roles]
    def decorator(f):
        @wraps(f)
        def decor(*args,**kwargs):
            if 'user_id' not in session:
                next_url = request.full_path if request.query_string else request.path
                return redirect(url_for('login') + f"?next={next_url}")
            if session.get('debe_cambiar'):
                return redirect(url_for('cambiar_password'))
            if session.get('rol') not in roles:
                return "<h1>⛔ No tienes permiso</h1><a href='/'>Volver</a>", 403
            return f(*args,**kwargs)
        return decor
    return decorator

def sede_requerida(sede_permitida):
    def decorator(f):
        @wraps(f)
        def decor(*args,**kwargs):
            user_sede = session.get('sede','TODAS')
            if user_sede != 'TODAS' and user_sede != sede_permitida:
                return f"<h1>⛔ No tienes permiso para {sede_permitida}</h1><p>Tu sede es {user_sede}</p><a href='/'>Volver</a>", 403
            return f(*args,**kwargs)
        return decor
    return decorator

def hora_a_min(h):
    try:
        hh, mm = map(int, h.split(':')); return hh*60+mm
    except: return 0

def calcular_precio_rango(h_inicio, h_fin):
    ini=hora_a_min(h_inicio); fin=hora_a_min(h_fin)
    if fin<=ini: fin+=1440
    total=0; m=ini
    while m<fin:
        h_actual=(m//60)%24
        precio=80 if (h_actual>=18 or h_actual<=2) else 60
        total+=precio*0.5; m+=30
    return int(total)

def hay_choque(cancha,fecha,n_ini_str,n_fin_str,sede,excluir_id=None):
    conn=get_reservas_conn(sede)
    q="SELECT * FROM reservas WHERE cancha=? AND fecha=? AND estado NOT IN ('NO_VINO','CANCELADO_SISTEMA')"
    params=[cancha,fecha]
    if excluir_id: q+=" AND id!=?"; params.append(excluir_id)
    reservas=conn.execute(q,params).fetchall(); conn.close()
    n_ini=hora_a_min(n_ini_str); n_fin=hora_a_min(n_fin_str)
    if n_fin<=n_ini: n_fin+=1440
    for r in reservas:
        e_ini_str=r['hora_inicio'] or r['hora'] or '00:00'
        e_fin_str=r['hora_fin']
        if not e_fin_str:
            e_ini=hora_a_min(e_ini_str); e_fin=e_ini+float(r['duracion'] or 1)*60
        else:
            e_ini=hora_a_min(e_ini_str); e_fin=hora_a_min(e_fin_str)
            if e_fin<=e_ini: e_fin+=1440
        if n_ini<e_fin and e_ini<n_fin: return dict(r)
    return None

# --- AUTH ---
@app.route('/')
def index():
    if 'user_id' in session:
        if session.get('debe_cambiar'):
            return redirect('/cambiar-password')
        sede = session.get('sede','TODAS')
        if session['rol']=='DUENO': return redirect('/resumen')
        if sede == 'COMAS': return redirect('/comas')
        if sede == 'JICAMARCA': return redirect('/jicamarca')
        if session['rol']=='RECEPCIONISTA': return redirect('/reservas')
        return redirect('/horarios')
    return redirect('/login')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        data=request.json or request.form
        username=(data.get('username') or '').strip()
        password=data.get('password') or ''
        conn=get_conn()
        user=conn.execute("SELECT * FROM usuarios WHERE username=?",(username,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id']=user['id']
            session['username']=user['username']
            session['rol']=user['rol']
            session['sede']=user['sede'] or 'TODAS'
            debe = int(user['debe_cambiar_password'] or 0) == 1
            if debe:
                session['debe_cambiar'] = True
                return jsonify({"ok":True,"debe_cambiar":True, "rol":user['rol'], "sede":session['sede']})
            else:
                session['debe_cambiar'] = False
                return jsonify({"ok":True,"debe_cambiar":False,"rol":user['rol'], "sede":session['sede']})
        return jsonify({"error":"Usuario o contraseña incorrecta"}),401
    return render_template('login.html')

@app.route('/register', methods=['GET','POST'])
@login_requerido
@rol_requerido(['DUENO'])
def register():
    conn = get_conn()
    if request.is_json:
        data = request.get_json()
        username = (data.get('username') or '').strip()
        password = (data.get('password') or '').strip()
        rol = (data.get('rol') or 'CLIENTE').strip()
        sede = (data.get('sede') or 'TODAS').strip()
        if not username or not password:
            conn.close()
            return jsonify({"ok": False, "error": "Usuario y clave obligatorios"}), 400
        existe = conn.execute("SELECT id FROM usuarios WHERE username=?", (username,)).fetchone()
        if existe:
            conn.close()
            return jsonify({"ok": False, "error": f"El usuario {username} ya existe"}), 400
        # NUEVO USUARIO SIEMPRE CON debe_cambiar=1
        conn.execute("INSERT INTO usuarios (username,password_hash,rol,sede,creado,debe_cambiar_password) VALUES (?,?,?,?,?,1)",
                     (username, generate_password_hash(password), rol, sede, datetime.now().isoformat()))
        conn.commit(); conn.close()
        return jsonify({"ok": True})

    msg = None
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','').strip()
        rol = request.form.get('rol','').strip()
        sede = request.form.get('sede','TODAS').strip()
        if not username or not password:
            msg = "Usuario y clave obligatorios"
        else:
            existe = conn.execute("SELECT id FROM usuarios WHERE username=?", (username,)).fetchone()
            if existe:
                msg = f"El usuario {username} ya existe"
            else:
                conn.execute("INSERT INTO usuarios (username,password_hash,rol,sede,creado,debe_cambiar_password) VALUES (?,?,?,?,?,1)",
                             (username, generate_password_hash(password), rol, sede, datetime.now().isoformat()))
                conn.commit()
                msg = f"✅ Usuario {username} creado como {rol} - {sede} (deberá cambiar clave)"
    
    usuarios = conn.execute("SELECT id, username, rol, sede, creado, debe_cambiar_password FROM usuarios ORDER BY sede, rol, username").fetchall()
    conn.close()
    return render_template('register.html', usuarios=usuarios, msg=msg)

@app.route('/api/usuarios/<int:user_id>', methods=['DELETE'])
@login_requerido
@rol_requerido('DUENO')
def api_eliminar_usuario(user_id):
    conn = get_conn()
    if user_id == session.get('user_id'):
        conn.close()
        return jsonify({"ok": False, "error": "No puedes borrarte a ti mismo"}), 400
    total_duenos = conn.execute("SELECT COUNT(*) FROM usuarios WHERE rol='DUENO'").fetchone()[0]
    u = conn.execute("SELECT rol FROM usuarios WHERE id=?", (user_id,)).fetchone()
    if u and u['rol'] == 'DUENO' and total_duenos <= 1:
        conn.close()
        return jsonify({"ok": False, "error": "No puedes borrar al último DUEÑO"}), 400
    conn.execute("DELETE FROM usuarios WHERE id=?", (user_id,))
    conn.commit(); conn.close()
    return jsonify({"ok": True})

@app.route('/logout')
def logout():
    session.clear(); return redirect('/login')

@app.route('/api/usuarios')
@login_requerido
@rol_requerido(['DUENO'])
def api_usuarios():
    conn=get_conn(); users=conn.execute("SELECT id,username,rol,sede,creado,debe_cambiar_password FROM usuarios ORDER BY id DESC").fetchall(); conn.close()
    return jsonify([dict(u) for u in users])

# --- APIS RESERVAS INDEPENDIENTES ---
@app.route('/api/reservas')
@login_requerido
def api_reservas():
    sede_user = session.get('sede','TODAS')
    sede_q = request.args.get('sede', sede_user)
    
    if sede_user != 'TODAS':
        sede_q = sede_user
    
    if sede_q == 'TODAS':
        conn1 = get_reservas_conn('COMAS')
        conn2 = get_reservas_conn('JICAMARCA')
        r1 = conn1.execute("SELECT *, 'COMAS' as sede FROM reservas ORDER BY fecha DESC, hora_inicio DESC").fetchall()
        r2 = conn2.execute("SELECT *, 'JICAMARCA' as sede FROM reservas ORDER BY fecha DESC, hora_inicio DESC").fetchall()
        conn1.close(); conn2.close()
        filas = [dict(x) for x in r1] + [dict(x) for x in r2]
        filas = sorted(filas, key=lambda x: (x['fecha'], x['hora_inicio']), reverse=True)
        return jsonify(filas)
    else:
        conn=get_reservas_conn(sede_q)
        filas=conn.execute("SELECT *, ? as sede FROM reservas ORDER BY fecha DESC, hora_inicio DESC",(sede_q,)).fetchall()
        conn.close()
        return jsonify([dict(r) for r in filas])

@app.route('/api/reservas/<int:id>', methods=['PUT','DELETE'])
@login_requerido
def editar_reserva(id):
    data_json = request.get_json(silent=True) or {}
    # Buscar en qué DB está
    conn_comas = get_reservas_conn('COMAS')
    actual = conn_comas.execute("SELECT *, 'COMAS' as sede FROM reservas WHERE id=?",(id,)).fetchone()
    conn_comas.close()
    sede_actual = 'COMAS'
    if not actual:
        conn_j = get_reservas_conn('JICAMARCA')
        actual = conn_j.execute("SELECT *, 'JICAMARCA' as sede FROM reservas WHERE id=?",(id,)).fetchone()
        conn_j.close()
        sede_actual = 'JICAMARCA'
    
    if not actual:
        return jsonify({"error":"No existe"}),404

    if session.get('sede') != 'TODAS' and actual['sede'] != session.get('sede'):
        return jsonify({"error":"No puedes modificar reservas de otra sede"}),403

    data = data_json if request.method=='PUT' else {}
    if request.method=='DELETE':
        if session.get('rol')!='DUENO': return jsonify({"error":"Solo dueño"}),403
        if request.args.get('hard')=='1':
            conn=get_reservas_conn(sede_actual); conn.execute("DELETE FROM reservas WHERE id=?",(id,)); conn.commit(); conn.close()
            registrar_historial(id, "BORRÓ DEFINITIVO", f"Reserva #{id} {sede_actual} borrada por {session.get('username')}")
            return jsonify({"ok":True,"borrado_real":True})
        else:
            conn=get_reservas_conn(sede_actual); conn.execute("UPDATE reservas SET estado='NO_VINO', actualizado_por=?, actualizado_en=? WHERE id=?",(session.get('username'), datetime.now().strftime("%d/%m/%Y %H:%M:%S"), id)); conn.commit(); conn.close()
            registrar_historial(id, "MARCÓ NO VINO", f"{sede_actual} marcado como NO VINO por {session.get('username')}")
            return jsonify({"ok":True})

    nueva_cancha = data.get('cancha', actual['cancha'])
    nueva_fecha = data.get('fecha', actual['fecha'])
    nuevo_ini = data.get('hora_inicio', actual['hora_inicio'] or actual['hora'])
    nuevo_fin = data.get('hora_fin', actual['hora_fin'])

    if any(k in data for k in ["hora_inicio","hora_fin","cancha","fecha"]):
        choque=hay_choque(nueva_cancha,nueva_fecha,nuevo_ini,nuevo_fin,sede_actual,excluir_id=id)
        if choque: return jsonify({"error":f"¡Choque! {choque['nombre']} {choque['hora_inicio']}-{choque['hora_fin']} en {choque['cancha']} ({sede_actual})"}),400
        if "hora_inicio" in data or "hora_fin" in data:
            nuevo_total = calcular_precio_rango(nuevo_ini, nuevo_fin)
            data['monto_total'] = nuevo_total
            dur = hora_a_min(nuevo_fin)-hora_a_min(nuevo_ini)
            if dur<=0: dur+=1440
            data['duracion'] = dur/60
            data['hora'] = nuevo_ini

    conn=get_reservas_conn(sede_actual); campos,valores=[],[] 
    for campo in ["nombre","cancha","fecha","hora","hora_inicio","hora_fin","duracion","telefono","monto_total","adelanto","estado","boleta","boleta_adelanto","boleta_final"]:
        if campo in data: campos.append(f"{campo}=?"); valores.append(data[campo])
    if campos:
        valores.append(id)
        conn.execute(f"UPDATE reservas SET {', '.join(campos)} WHERE id=?",valores)
        conn.execute("UPDATE reservas SET actualizado_por=?, actualizado_en=? WHERE id=?", (session.get('username'), datetime.now().strftime("%d/%m/%Y %H:%M:%S"), id))
        conn.commit()
    conn.close()
    registrar_historial(id, f"MODIFICÓ - {', '.join(data.keys())} [{sede_actual}]", str(data))
    return jsonify({"ok":True})

# --- HISTORIAL API ---
@app.route('/api/historial/<int:reserva_id>')
@login_requerido
def api_historial(reserva_id):
    conn=get_conn()
    filas=conn.execute("SELECT * FROM historial_reservas WHERE reserva_id=? ORDER BY id DESC",(reserva_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in filas])

@app.route('/api/resumen')
@login_requerido
@rol_requerido('DUENO')
def api_resumen():
    sede_q = request.args.get('sede','TODAS')
    lista_sedes = ['COMAS','JICAMARCA'] if sede_q=='TODAS' else [sede_q]
    todas=[]
    for s in lista_sedes:
        conn=get_reservas_conn(s)
        filas=conn.execute("SELECT *, ? as sede FROM reservas",(s,)).fetchall()
        conn.close()
        todas.extend([dict(r) for r in filas])
    return jsonify(todas)

# --- PAGINAS PROTEGIDAS SEPARADAS ---
@app.route('/resumen')
@rol_requerido(['DUENO'])
def resumen(): return render_template('resumen.html')

@app.route('/comas')
@login_requerido
@sede_requerida('COMAS')
def comas_page():
    return render_template('panel.html', sede='COMAS')

@app.route('/jicamarca')
@login_requerido
@sede_requerida('JICAMARCA')
def jicamarca_page():
    return render_template('panel.html', sede='JICAMARCA')

@app.route('/reservas', methods=['GET','POST'])
@rol_requerido(['DUENO','RECEPCIONISTA'])
def ver_reservas():
    sede_q = request.args.get('sede','COMAS')
    if session.get('sede') != 'TODAS':
        sede_q = session.get('sede')
        if request.method == 'GET':
            return redirect(f"/{sede_q.lower()}")

    if request.method=='POST':
        data=request.json or {}
        h_ini=data.get('hora_inicio'); h_fin=data.get('hora_fin')
        if not h_ini or not h_fin: return jsonify({"error":"Falta hora"}),400
        
        sede_reserva = data.get('sede') or request.args.get('sede') or sede_q or 'COMAS'
        if session.get('sede') != 'TODAS':
            sede_reserva = session.get('sede')

        choque=hay_choque(data['cancha'],data['fecha'],h_ini,h_fin,sede_reserva)
        if choque: return jsonify({"error":f"¡Ocupado! {choque['nombre']} {choque['hora_inicio']}-{choque['hora_fin']} en {sede_reserva}"}),400
        precio_total=calcular_precio_rango(h_ini,h_fin)
        if data.get('monto_total'):
            try: precio_total=int(data.get('monto_total'))
            except: pass
        dur_min=hora_a_min(h_fin)-hora_a_min(h_ini); 
        if dur_min<=0: dur_min+=1440
        
        creado_por = session.get('username','-')
        creado_en = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        
        conn=get_reservas_conn(sede_reserva)
        cur = conn.cursor()
        cur.execute("INSERT INTO reservas (nombre,cancha,fecha,hora,hora_inicio,hora_fin,duracion,telefono,monto_total,adelanto,estado,boleta_adelanto,boleta_final,creado,creado_por,actualizado_por,actualizado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (data['nombre'],data['cancha'],data['fecha'],h_ini,h_ini,h_fin,dur_min/60,data.get('telefono',''),precio_total,int(data.get('adelanto') or 0),data.get('estado','ADELANTO'),data.get('boleta_adelanto',''),data.get('boleta_final',''),datetime.now().isoformat(), creado_por, creado_por, creado_en))
        reserva_id = cur.lastrowid
        conn.commit(); conn.close()
        
        registrar_historial(reserva_id, "CREÓ RESERVA", f"{sede_reserva} {data['cancha']} {data['fecha']} {h_ini}-{h_fin} S/{precio_total} por {creado_por}")
        return jsonify({"ok":True, "id": reserva_id, "sede": sede_reserva})
    
    return render_template('panel.html', sede=sede_q)

@app.route('/admin-dueno')
@rol_requerido(['DUENO'])
def admin_dueno(): 
    try: return render_template('admin_dueno.html')
    except: return render_template('admin.html')

@app.route('/horarios')
@login_requerido
def horarios_page():
    sede = request.args.get('sede') or session.get('sede')
    if session.get('sede') != 'TODAS' and session.get('sede') != sede:
        sede = session.get('sede')
    if not sede or sede == 'TODAS':
        sede = 'COMAS'
    return render_template('horarios.html', sede=sede)

@app.route('/disponibilidad')
@login_requerido
def disponibilidad(): return render_template('horarios.html')

@app.route('/horarios-dia')
@login_requerido
def horarios_dia_page():
    sede = request.args.get('sede') or session.get('sede')
    if session.get('sede') != 'TODAS' and session.get('sede') != sede:
        sede = session.get('sede')
    if not sede or sede == 'TODAS':
        sede = 'COMAS'
    fecha = request.args.get('fecha')
    return render_template('horarios_dia.html', sede=sede, fecha=fecha)

@app.route('/api/horarios')
@login_requerido
def api_horarios():
    sede = request.args.get('sede') or session.get('sede')
    if sede == 'TODAS':
        sede = request.args.get('sede') or 'COMAS'
    if not sede:
        return jsonify({"error":"Falta sede"}), 400
    conn = get_reservas_conn(sede)
    cur = conn.cursor()
    cur.execute("SELECT fecha, cancha, hora_inicio, hora_fin, estado FROM reservas WHERE estado != 'NO_VINO'")
    rows = cur.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/horarios-dia')
@login_requerido
def api_horarios_dia():
    sede = request.args.get('sede') or session.get('sede')
    fecha = request.args.get('fecha')
    if sede == 'TODAS':
        sede = request.args.get('sede') or 'COMAS'
    if not fecha or not sede:
        return jsonify({"error":"Falta sede o fecha"}), 400
    conn = get_reservas_conn(sede)
    cur = conn.cursor()
    cur.execute("SELECT * FROM reservas WHERE fecha=? AND estado != 'NO_VINO' ORDER BY cancha, hora_inicio", (fecha,))
    rows = cur.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

# --- CAMBIO OBLIGATORIO DE CONTRASEÑA ---
@app.route('/cambiar-password', methods=['GET'])
@login_requerido
def cambiar_password():
    return render_template('cambiar_password.html')

@app.route('/api/cambiar-password', methods=['POST'])
@login_requerido
def api_cambiar_password():
    data=request.json or {}
    nueva=(data.get('nueva') or '').strip()
    actual=data.get('actual')
    confirma=(data.get('confirma') or '').strip()

    if len(nueva)<6:
        return jsonify({"error":"Mínimo 6 caracteres"}),400
    if confirma and nueva != confirma:
        return jsonify({"error":"Las contraseñas no coinciden"}),400

    conn=get_conn()
    user=conn.execute("SELECT * FROM usuarios WHERE id=?",(session['user_id'],)).fetchone()
    
    # Si NO es primer ingreso, validar actual
    if not session.get('debe_cambiar'):
        if not actual or not check_password_hash(user['password_hash'], actual):
            conn.close()
            return jsonify({"error":"Contraseña actual incorrecta"}),400
    
    conn.execute("UPDATE usuarios SET password_hash=?, debe_cambiar_password=0 WHERE id=?",
                 (generate_password_hash(nueva), session['user_id']))
    conn.commit(); conn.close()
    session['debe_cambiar'] = False
    return jsonify({"ok":True})

@app.route('/api/reset-password/<int:user_id>', methods=['POST'])
@login_requerido
@rol_requerido(['DUENO'])
def reset_password(user_id):
    data=request.json or {}
    nueva=data.get('nueva') or '123456'
    conn=get_conn()
    conn.execute("UPDATE usuarios SET password_hash=?, debe_cambiar_password=1 WHERE id=?",
                 (generate_password_hash(nueva), user_id))
    conn.commit(); conn.close()
    return jsonify({"ok":True, "msg":f"Reseteado a {nueva}, deberá cambiar al entrar"})

if __name__=='__main__':
    app.run(port=5000,debug=True)