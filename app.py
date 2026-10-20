import calendar
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from datetime import date, datetime, timedelta
from functools import wraps
from io import BytesIO
from zoneinfo import ZoneInfo

import openpyxl
from flask import (
    Flask, abort, flash, redirect, render_template, request, send_file,
    session, url_for,
)
from openpyxl.styles import Font
from werkzeug.middleware.proxy_fix import ProxyFix

from db import CUPO_MAXIMO, DB_PATH, MESES_ADELANTE, MISAS, get_conn, init_db

ZONA = ZoneInfo("America/New_York")

app = Flask(__name__)
# Detras de Traefik: usar la IP real del cliente y el esquema https.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def _clave_secreta():
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    ruta = DB_PATH.parent / "secret.key"
    if not ruta.exists():
        ruta.write_text(secrets.token_hex(32))
    return ruta.read_text().strip()


app.secret_key = _clave_secreta()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)


# ---------- Fechas ----------

def hoy():
    return datetime.now(ZONA).date()


def sumar_meses(d, n):
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def fin_ventana():
    return sumar_meses(hoy(), MESES_ADELANTE)


def domingos_disponibles():
    inicio = hoy()
    fin = fin_ventana()
    d = inicio + timedelta(days=(6 - inicio.weekday()) % 7)
    domingos = []
    while d <= fin:
        domingos.append(d)
        d += timedelta(days=7)
    return domingos


MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def fecha_larga(iso):
    d = date.fromisoformat(iso)
    return f"{DIAS_ES[d.weekday()]} {d.day} de {MESES_ES[d.month - 1]} de {d.year}"


def fecha_corta(iso):
    d = date.fromisoformat(iso)
    return f"{d.day} {MESES_ES[d.month - 1][:3]}"


def hora12(hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


app.jinja_env.filters["fecha_larga"] = fecha_larga
app.jinja_env.filters["fecha_corta"] = fecha_corta
app.jinja_env.filters["hora12"] = hora12


# ---------- Turnos (misas dominicales y especiales) ----------

def etiqueta_especial(e):
    nombre = e["descripcion"] or "Misa especial"
    return f"{hora12(e['hora'])} · {nombre}"


def especiales_en_ventana(conn):
    return conn.execute(
        "SELECT * FROM misas_especiales WHERE fecha >= ? AND fecha <= ? ORDER BY fecha, hora, id",
        (hoy().isoformat(), fin_ventana().isoformat()),
    ).fetchall()


def todos_especiales(conn):
    return {f"E{e['id']}": e for e in conn.execute("SELECT * FROM misas_especiales")}


def etiqueta_turno(misa, especiales):
    if misa in MISAS:
        return MISAS[misa]
    e = especiales.get(misa)
    return etiqueta_especial(e) if e else "Misa eliminada"


def cupo_turno(conn, fecha, misa):
    """Cupo del turno, o None si el turno no existe (misa invalida o fecha que no corresponde)."""
    if misa in MISAS:
        return CUPO_MAXIMO
    if misa.startswith("E") and misa[1:].isdigit():
        e = conn.execute("SELECT fecha, cupo FROM misas_especiales WHERE id=?", (int(misa[1:]),)).fetchone()
        if e and e["fecha"] == fecha:
            return e["cupo"]
    return None


def conteos(conn):
    filas = conn.execute(
        "SELECT fecha, misa, COUNT(*) AS n FROM registros GROUP BY fecha, misa"
    ).fetchall()
    return {(f["fecha"], f["misa"]): f["n"] for f in filas}


def insertar_registro(conn, nombre, apellido, celular, fecha, misa, excluir_id=None):
    """Devuelve 'ok', 'lleno', 'duplicado' o 'invalido'. Atomico: bloquea escritura mientras cuenta e inserta."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        cupo = cupo_turno(conn, fecha, misa)
        if cupo is None:
            conn.rollback()
            return "invalido"
        n = conn.execute(
            "SELECT COUNT(*) FROM registros WHERE fecha=? AND misa=? AND id IS NOT ?",
            (fecha, misa, excluir_id),
        ).fetchone()[0]
        if n >= cupo:
            conn.rollback()
            return "lleno"
        conn.execute(
            "INSERT INTO registros (nombre, apellido, celular, fecha, misa) VALUES (?,?,?,?,?)",
            (nombre, apellido, celular, fecha, misa),
        )
        conn.commit()
        return "ok"
    except sqlite3.IntegrityError:
        conn.rollback()
        return "duplicado"


# ---------- Utilidades ----------

def normalizar_celular(valor):
    digitos = re.sub(r"\D", "", valor or "")
    if len(digitos) == 11 and digitos.startswith("1"):
        digitos = digitos[1:]
    if len(digitos) != 10:
        return None
    return f"({digitos[:3]}) {digitos[3:6]}-{digitos[6:]}"


def limpiar_texto(valor, largo=60):
    return re.sub(r"\s+", " ", (valor or "").strip())[:largo]


ADMIN_HORAS = 8


def es_admin():
    return time.time() - session.get("admin_ts", 0) < ADMIN_HORAS * 3600


def _huella_clave(clave):
    return hashlib.sha256(("parqueo:" + clave.strip().casefold()).encode()).hexdigest()[:20]


def acceso_servidor():
    """True si el visitante ya ingreso la clave de registro (o no hay clave, o es administrador)."""
    if es_admin():
        return True
    esperada = clave_registro()
    if not esperada:
        return True
    return session.get("clave_ok") == _huella_clave(esperada)


def desbloquear():
    esperada = clave_registro()
    if esperada:
        session["clave_ok"] = _huella_clave(esperada)
        session.permanent = True


def token_csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = token_csrf
app.jinja_env.globals["es_admin"] = es_admin
app.jinja_env.globals["acceso_servidor"] = acceso_servidor
app.jinja_env.globals["MISAS"] = MISAS
app.jinja_env.globals["CUPO"] = CUPO_MAXIMO


def requiere_admin(f):
    @wraps(f)
    def envoltura(*args, **kwargs):
        if not es_admin():
            return redirect(url_for("admin_login"))
        if request.method == "POST":
            enviado = request.form.get("csrf", "")
            if not hmac.compare_digest(enviado, session.get("csrf", "")):
                abort(400)
        return f(*args, **kwargs)

    return envoltura


# ---------- Clave de registro ----------

MAX_INTENTOS = 8
VENTANA_SEG = 600
_intentos = {}


def clave_registro(conn=None):
    cerrar = conn is None
    conn = conn or get_conn()
    fila = conn.execute("SELECT valor FROM config WHERE clave='clave_registro'").fetchone()
    if cerrar:
        conn.close()
    return fila["valor"] if fila else ""


def _recientes():
    ahora = time.time()
    lista = [t for t in _intentos.get(request.remote_addr, []) if ahora - t < VENTANA_SEG]
    _intentos[request.remote_addr] = lista
    return lista


def bloqueado_por_intentos():
    return len(_recientes()) >= MAX_INTENTOS


def registrar_intento_fallido():
    _recientes().append(time.time())


def clave_correcta(enviada, esperada):
    a = (enviada or "").strip().casefold().encode()
    b = esperada.strip().casefold().encode()
    return hmac.compare_digest(a, b)


# ---------- Publico ----------

def construir_meses(cuentas, domingos, fechas_especiales):
    disponibles = {d.isoformat() for d in domingos}
    primero = hoy().replace(day=1)
    ultimo = fin_ventana()
    meses = []
    y, m = primero.year, primero.month
    cal = calendar.Calendar(firstweekday=6)
    while (y, m) <= (ultimo.year, ultimo.month):
        semanas = []
        for semana in cal.monthdatescalendar(y, m):
            fila = []
            for d in semana:
                if d.month != m:
                    fila.append(None)
                    continue
                iso = d.isoformat()
                celda = {"dia": d.day, "fecha": iso, "domingo": False,
                         "especial": iso in fechas_especiales}
                if iso in disponibles:
                    celda["domingo"] = True
                    celda["cuentas"] = {k: cuentas.get((iso, k), 0) for k in MISAS}
                fila.append(celda)
            semanas.append(fila)
        meses.append({"etiqueta": f"{MESES_ES[m - 1]} {y}", "semanas": semanas})
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return meses


def pagina_registro(datos=None, seleccionadas=(), especiales_sel=()):
    conn = get_conn()
    cuentas = conteos(conn)
    esp = especiales_en_ventana(conn)
    conn.close()
    especiales = []
    for e in esp:
        n = cuentas.get((e["fecha"], f"E{e['id']}"), 0)
        especiales.append({
            "id": e["id"], "fecha": e["fecha"], "etiqueta": etiqueta_especial(e),
            "n": n, "cupo": e["cupo"], "lleno": n >= e["cupo"],
        })
    return render_template(
        "index.html",
        meses=construir_meses(cuentas, domingos_disponibles(), {e["fecha"] for e in esp}),
        especiales=especiales,
        datos=datos or {"misa": "1030"},
        seleccionadas=set(seleccionadas),
        especiales_sel={str(x) for x in especiales_sel},
        requiere_clave=bool(clave_registro()),
        titulo_pagina="Registro de servidores",
    )


@app.route("/")
def index():
    return pagina_registro()


@app.route("/registrar", methods=["POST"])
def registrar():
    nombre = limpiar_texto(request.form.get("nombre"))
    apellido = limpiar_texto(request.form.get("apellido"))
    celular = normalizar_celular(request.form.get("celular"))
    misa = request.form.get("misa", "")
    fechas = sorted(set(request.form.getlist("fechas")))
    ids_especiales = sorted({x for x in request.form.getlist("especiales") if x.isdigit()}, key=int)
    validas = {d.isoformat() for d in domingos_disponibles()}

    datos = {"nombre": nombre, "apellido": apellido,
             "celular": request.form.get("celular", ""), "misa": misa if misa in MISAS else "1030"}
    errores = []
    if not nombre:
        errores.append("Nombre es requerido.")
    if not apellido:
        errores.append("Apellido es requerido.")
    if not celular:
        errores.append("El celular debe tener 10 digitos.")
    if not fechas and not ids_especiales:
        errores.append("Elige al menos un domingo en el calendario o una misa especial.")
    if fechas and misa not in MISAS:
        errores.append("Elige un horario de misa para los domingos.")
    if any(f not in validas for f in fechas):
        errores.append("Una de las fechas elegidas no esta disponible.")
    if errores:
        for e in errores:
            flash(e, "error")
        return pagina_registro(datos, fechas, ids_especiales)

    esperada = clave_registro()
    if esperada:
        if bloqueado_por_intentos():
            flash("Demasiados intentos con clave incorrecta. Espera unos minutos e inténtalo de nuevo.", "error")
            return pagina_registro(datos, fechas, ids_especiales)
        if not clave_correcta(request.form.get("clave_acceso"), esperada):
            registrar_intento_fallido()
            flash("La clave de registro no es correcta. Pídela al coordinador del ministerio.", "error")
            return pagina_registro(datos, fechas, ids_especiales)
        desbloquear()

    conn = get_conn()
    especiales = {e["id"]: e for e in especiales_en_ventana(conn)}
    turnos = [(f, misa, f"{fecha_corta(f)} {MISAS[misa]}") for f in fechas]
    for x in ids_especiales:
        e = especiales.get(int(x))
        if e:
            turnos.append((e["fecha"], f"E{e['id']}", f"{fecha_corta(e['fecha'])} {etiqueta_especial(e)}"))
        else:
            turnos.append((None, None, "una misa especial (ya no esta disponible)"))

    ok, omitidos = [], []
    for fecha, clave, texto in turnos:
        if fecha is None:
            omitidos.append(texto)
            continue
        resultado = insertar_registro(conn, nombre, apellido, celular, fecha, clave)
        if resultado == "ok":
            ok.append(texto)
        elif resultado == "lleno":
            omitidos.append(f"{texto} (lleno)")
        elif resultado == "duplicado":
            omitidos.append(f"{texto} (ya estabas inscrito)")
        else:
            omitidos.append(f"{texto} (no disponible)")
    conn.close()

    if ok:
        flash(f"Listo, {nombre}. Quedaste registrado en: {'; '.join(ok)}.", "success")
    if omitidos:
        flash(f"No se registro: {'; '.join(omitidos)}.", "error")
    if not ok:
        return pagina_registro(datos, [], [])
    return redirect(url_for("index"))


def exigir_acceso_servidor():
    if acceso_servidor():
        return None
    flash("Ingresa la clave de registro para ver esta sección.", "error")
    return redirect(url_for("index"))


@app.route("/desbloquear", methods=["POST"])
def desbloquear_ruta():
    destino = {"turnos": "turnos", "logistica": "logistica"}.get(request.form.get("destino", ""), "index")
    esperada = clave_registro()
    if esperada:
        if bloqueado_por_intentos():
            flash("Demasiados intentos con clave incorrecta. Espera unos minutos e inténtalo de nuevo.", "error")
            return redirect(url_for("index"))
        if not clave_correcta(request.form.get("clave_acceso"), esperada):
            registrar_intento_fallido()
            flash("La clave de registro no es correcta. Pídela al coordinador del ministerio.", "error")
            return redirect(url_for("index"))
        desbloquear()
    flash("Acceso habilitado.", "success")
    return redirect(url_for(destino))


@app.route("/turnos")
def turnos():
    bloqueo = exigir_acceso_servidor()
    if bloqueo:
        return bloqueo
    conn = get_conn()
    filas = conn.execute(
        "SELECT fecha, misa, nombre, apellido FROM registros ORDER BY fecha, misa, creado, id"
    ).fetchall()
    esp = especiales_en_ventana(conn)
    conn.close()
    por_turno = {}
    for f in filas:
        por_turno.setdefault((f["fecha"], f["misa"]), []).append(f"{f['nombre']} {f['apellido']}")

    dias = {}
    for d in domingos_disponibles():
        iso = d.isoformat()
        dias[iso] = [{"etiqueta": v, "especial": False, "cupo": CUPO_MAXIMO,
                      "nombres": por_turno.get((iso, k), [])} for k, v in MISAS.items()]
    for e in esp:
        clave = f"E{e['id']}"
        dias.setdefault(e["fecha"], []).append({
            "etiqueta": etiqueta_especial(e), "especial": True, "cupo": e["cupo"],
            "nombres": por_turno.get((e["fecha"], clave), []),
        })
    domingos = [{"fecha": f, "misas": dias[f]} for f in sorted(dias)]
    return render_template("turnos.html", domingos=domingos, titulo_pagina="Turnos por domingo")


@app.route("/logistica")
def logistica():
    bloqueo = exigir_acceso_servidor()
    if bloqueo:
        return bloqueo
    return render_template("logistica.html", titulo_pagina="Logística Parqueo")


# ---------- Administrador ----------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    clave = os.environ.get("ADMIN_PASSWORD", "")
    if request.method == "POST":
        if not clave:
            flash("El acceso de administrador no esta configurado en el servidor.", "error")
        elif hmac.compare_digest(request.form.get("password", ""), clave):
            session.pop("csrf", None)
            session["admin_ts"] = time.time()
            session.permanent = True
            return redirect(url_for("admin"))
        else:
            flash("Contraseña incorrecta.", "error")
    return render_template("admin_login.html", titulo_pagina="Administrador")


@app.route("/admin/salir", methods=["POST"])
@requiere_admin
def admin_salir():
    session.pop("admin_ts", None)
    session.pop("csrf", None)
    return redirect(url_for("index"))


def registros_filtrados(fecha):
    conn = get_conn()
    if fecha:
        filas = conn.execute(
            "SELECT * FROM registros WHERE fecha=? ORDER BY fecha, misa, creado, id", (fecha,)
        ).fetchall()
    else:
        filas = conn.execute("SELECT * FROM registros ORDER BY fecha, misa, creado, id").fetchall()
    fechas = [r["fecha"] for r in conn.execute("SELECT DISTINCT fecha FROM registros ORDER BY fecha")]
    especiales = todos_especiales(conn)
    conn.close()
    filas = [{**dict(r), "turno": etiqueta_turno(r["misa"], especiales)} for r in filas]
    return filas, fechas, especiales


def opciones_turnos(incluir=None):
    """Todos los turnos disponibles como (valor 'fecha|misa', etiqueta), en orden cronologico."""
    conn = get_conn()
    esp = especiales_en_ventana(conn)
    todos = todos_especiales(conn)
    conn.close()
    opciones = {}
    for d in domingos_disponibles():
        iso = d.isoformat()
        for k, v in MISAS.items():
            opciones[(iso, k)] = f"{fecha_larga(iso)} · {v}"
    for e in esp:
        iso = e["fecha"]
        opciones[(iso, f"E{e['id']}")] = f"{fecha_larga(iso)} · {etiqueta_especial(e)}"
    if incluir and incluir not in opciones:
        f, k = incluir
        opciones[incluir] = f"{fecha_larga(f)} · {etiqueta_turno(k, todos)}"
    return [(f"{f}|{k}", t) for (f, k), t in sorted(opciones.items())]


@app.route("/admin")
@requiere_admin
def admin():
    fecha = request.args.get("fecha", "")
    filas, fechas_con_datos, _ = registros_filtrados(fecha)
    conn = get_conn()
    fechas_esp = {e["fecha"] for e in especiales_en_ventana(conn)}
    conn.close()
    todas = sorted({d.isoformat() for d in domingos_disponibles()} | set(fechas_con_datos) | fechas_esp)
    return render_template("admin.html", filas=filas, fechas=todas, filtro=fecha,
                           titulo_pagina="Administrar registros")


def datos_formulario_admin():
    errores = []
    nombre = limpiar_texto(request.form.get("nombre"))
    apellido = limpiar_texto(request.form.get("apellido"))
    celular = normalizar_celular(request.form.get("celular"))
    turno = request.form.get("turno", "")
    fecha, _, misa = turno.partition("|")
    try:
        date.fromisoformat(fecha)
    except ValueError:
        errores.append("Turno invalido.")
    if not nombre or not apellido:
        errores.append("Nombre y apellido son requeridos.")
    if not celular:
        errores.append("El celular debe tener 10 digitos.")
    return errores, (nombre, apellido, celular, fecha, misa)


def formulario_registro(datos, titulo, **extra):
    incluir = (datos["fecha"], datos["misa"]) if datos.get("fecha") and datos.get("misa") else None
    return render_template("admin_form.html", datos=datos, opciones=opciones_turnos(incluir),
                           turno_actual=f"{datos.get('fecha', '')}|{datos.get('misa', '')}",
                           titulo=titulo, titulo_pagina=titulo, **extra)


@app.route("/admin/nuevo", methods=["GET", "POST"])
@requiere_admin
def admin_nuevo():
    if request.method == "POST":
        errores, datos = datos_formulario_admin()
        if not errores:
            conn = get_conn()
            res = insertar_registro(conn, *datos)
            conn.close()
            if res == "ok":
                flash("Servidor añadido.", "success")
                return redirect(url_for("admin", fecha=datos[3]))
            errores.append({"lleno": "Ese turno ya esta lleno.",
                            "invalido": "Ese turno no existe."}.get(
                                res, "Ese celular ya esta inscrito en ese turno."))
        for e in errores:
            flash(e, "error")
        d = dict(zip(("nombre", "apellido", "celular", "fecha", "misa"), datos))
        return formulario_registro(d, "Añadir servidor")
    return formulario_registro({"misa": "1030"}, "Añadir servidor")


@app.route("/admin/editar/<int:id>", methods=["GET", "POST"])
@requiere_admin
def admin_editar(id):
    conn = get_conn()
    fila = conn.execute("SELECT * FROM registros WHERE id=?", (id,)).fetchone()
    conn.close()
    if fila is None:
        flash("Registro no encontrado.", "error")
        return redirect(url_for("admin"))

    if request.method == "POST":
        errores, datos = datos_formulario_admin()
        if not errores:
            nombre, apellido, celular, fecha, misa = datos
            conn = get_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                cupo = cupo_turno(conn, fecha, misa)
                n = conn.execute(
                    "SELECT COUNT(*) FROM registros WHERE fecha=? AND misa=? AND id!=?",
                    (fecha, misa, id),
                ).fetchone()[0]
                if cupo is None:
                    conn.rollback()
                    errores.append("Ese turno no existe.")
                elif n >= cupo:
                    conn.rollback()
                    errores.append("Ese turno ya esta lleno.")
                else:
                    conn.execute(
                        "UPDATE registros SET nombre=?, apellido=?, celular=?, fecha=?, misa=? WHERE id=?",
                        (nombre, apellido, celular, fecha, misa, id),
                    )
                    conn.commit()
                    flash("Registro actualizado.", "success")
                    conn.close()
                    return redirect(url_for("admin", fecha=fecha))
            except sqlite3.IntegrityError:
                conn.rollback()
                errores.append("Ese celular ya esta inscrito en ese turno.")
            conn.close()
        for e in errores:
            flash(e, "error")
        d = dict(zip(("nombre", "apellido", "celular", "fecha", "misa"), datos))
        d["id"] = id
        return formulario_registro(d, "Modificar servidor", editar=True)
    return formulario_registro(dict(fila), "Modificar servidor", editar=True)


@app.route("/admin/eliminar/<int:id>", methods=["POST"])
@requiere_admin
def admin_eliminar(id):
    conn = get_conn()
    conn.execute("DELETE FROM registros WHERE id=?", (id,))
    conn.commit()
    conn.close()
    flash("Registro eliminado.", "success")
    return redirect(url_for("admin", fecha=request.form.get("fecha", "")))


# ---------- Administrador: clave de registro ----------

@app.route("/admin/clave", methods=["GET", "POST"])
@requiere_admin
def admin_clave():
    if request.method == "POST":
        nueva = request.form.get("clave", "").strip()
        conn = get_conn()
        if request.form.get("accion") == "quitar":
            conn.execute("DELETE FROM config WHERE clave='clave_registro'")
            conn.commit()
            conn.close()
            flash("Clave eliminada. El registro queda abierto sin clave.", "success")
            return redirect(url_for("admin_clave"))
        if not 4 <= len(nueva) <= 40:
            conn.close()
            flash("La clave debe tener entre 4 y 40 caracteres.", "error")
            return redirect(url_for("admin_clave"))
        conn.execute(
            "INSERT INTO config (clave, valor) VALUES ('clave_registro', ?) "
            "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (nueva,))
        conn.commit()
        conn.close()
        flash("Clave de registro guardada. Compártela con los servidores.", "success")
        return redirect(url_for("admin_clave"))
    return render_template("admin_clave.html", clave=clave_registro(), titulo_pagina="Clave de registro")


# ---------- Administrador: misas especiales ----------

@app.route("/admin/especiales")
@requiere_admin
def admin_especiales():
    conn = get_conn()
    filas = conn.execute("SELECT * FROM misas_especiales ORDER BY fecha, hora, id").fetchall()
    cuentas = conteos(conn)
    conn.close()
    especiales = [{**dict(e), "etiqueta": etiqueta_especial(e),
                   "n": cuentas.get((e["fecha"], f"E{e['id']}"), 0),
                   "pasada": e["fecha"] < hoy().isoformat()} for e in filas]
    return render_template("admin_especiales.html", especiales=especiales,
                           titulo_pagina="Misas especiales")


def datos_especial():
    errores = []
    fecha = request.form.get("fecha", "")
    hora = request.form.get("hora", "")
    descripcion = limpiar_texto(request.form.get("descripcion"), 80)
    try:
        d = date.fromisoformat(fecha)
        if d < hoy() or d > fin_ventana():
            errores.append(f"La fecha debe estar entre hoy y el {fecha_larga(fin_ventana().isoformat())}.")
    except ValueError:
        errores.append("Fecha invalida.")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", hora):
        errores.append("Hora invalida.")
    try:
        cupo = int(request.form.get("cupo", CUPO_MAXIMO))
        if not 1 <= cupo <= 50:
            raise ValueError
    except ValueError:
        errores.append("El cupo debe ser un numero entre 1 y 50.")
        cupo = CUPO_MAXIMO
    return errores, {"fecha": fecha, "hora": hora, "descripcion": descripcion, "cupo": cupo}


@app.route("/admin/especiales/nueva", methods=["GET", "POST"])
@requiere_admin
def admin_especial_nueva():
    datos = {"fecha": "", "hora": "19:00", "descripcion": "", "cupo": CUPO_MAXIMO}
    if request.method == "POST":
        errores, datos = datos_especial()
        if not errores:
            conn = get_conn()
            conn.execute(
                "INSERT INTO misas_especiales (fecha, hora, descripcion, cupo) VALUES (?,?,?,?)",
                (datos["fecha"], datos["hora"], datos["descripcion"], datos["cupo"]),
            )
            conn.commit()
            conn.close()
            flash("Misa especial creada. Ya aparece en el registro publico.", "success")
            return redirect(url_for("admin_especiales"))
        for e in errores:
            flash(e, "error")
    return render_template("admin_especial_form.html", datos=datos, titulo="Nueva misa especial",
                           minimo=hoy().isoformat(), maximo=fin_ventana().isoformat(),
                           titulo_pagina="Nueva misa especial")


@app.route("/admin/especiales/editar/<int:id>", methods=["GET", "POST"])
@requiere_admin
def admin_especial_editar(id):
    conn = get_conn()
    fila = conn.execute("SELECT * FROM misas_especiales WHERE id=?", (id,)).fetchone()
    if fila is None:
        conn.close()
        flash("Misa especial no encontrada.", "error")
        return redirect(url_for("admin_especiales"))
    datos = dict(fila)
    if request.method == "POST":
        errores, datos = datos_especial()
        if not errores:
            clave = f"E{id}"
            inscritos = conn.execute("SELECT COUNT(*) FROM registros WHERE misa=?", (clave,)).fetchone()[0]
            if datos["cupo"] < inscritos:
                errores.append(f"Ya hay {inscritos} servidores inscritos; el cupo no puede ser menor.")
            else:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE misas_especiales SET fecha=?, hora=?, descripcion=?, cupo=? WHERE id=?",
                    (datos["fecha"], datos["hora"], datos["descripcion"], datos["cupo"], id),
                )
                conn.execute("UPDATE registros SET fecha=? WHERE misa=?", (datos["fecha"], clave))
                conn.commit()
                conn.close()
                flash("Misa especial actualizada.", "success")
                return redirect(url_for("admin_especiales"))
        for e in errores:
            flash(e, "error")
    conn.close()
    return render_template("admin_especial_form.html", datos=datos, titulo="Modificar misa especial",
                           minimo=hoy().isoformat(), maximo=fin_ventana().isoformat(), editar=True,
                           titulo_pagina="Modificar misa especial")


@app.route("/admin/especiales/eliminar/<int:id>", methods=["POST"])
@requiere_admin
def admin_especial_eliminar(id):
    conn = get_conn()
    conn.execute("BEGIN IMMEDIATE")
    borrados = conn.execute("DELETE FROM registros WHERE misa=?", (f"E{id}",)).rowcount
    conn.execute("DELETE FROM misas_especiales WHERE id=?", (id,))
    conn.commit()
    conn.close()
    flash(f"Misa especial eliminada ({borrados} inscripciones borradas).", "success")
    return redirect(url_for("admin_especiales"))


@app.route("/admin/exportar")
@requiere_admin
def admin_exportar():
    filas, _, _ = registros_filtrados("")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Turnos de Parqueo"
    ws.append(["Fecha", "Misa", "Nombre", "Apellido", "Celular"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in filas:
        ws.append([r["fecha"], r["turno"], r["nombre"], r["apellido"], r["celular"]])
    for col in ws.columns:
        ancho = max((len(str(c.value)) for c in col if c.value is not None), default=10)
        ws.column_dimensions[col[0].column_letter].width = ancho + 2
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return send_file(
        buffer, as_attachment=True,
        download_name=f"turnos_parqueo_{hoy().isoformat()}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.after_request
def cabeceras(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if request.path.startswith("/admin"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5002)
