import calendar
import hmac
import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

import openpyxl
from flask import (
    Flask, abort, flash, redirect, render_template, request, send_file,
    session, url_for,
)
from openpyxl.styles import Font

from db import CUPO_MAXIMO, DB_PATH, MESES_ADELANTE, MISAS, get_conn, init_db

ZONA = ZoneInfo("America/New_York")

app = Flask(__name__)


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
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)


# ---------- Fechas ----------

def hoy():
    return datetime.now(ZONA).date()


def sumar_meses(d, n):
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def domingos_disponibles():
    inicio = hoy()
    fin = sumar_meses(inicio, MESES_ADELANTE)
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


app.jinja_env.filters["fecha_larga"] = fecha_larga
app.jinja_env.filters["fecha_corta"] = fecha_corta


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


def conteos(conn):
    filas = conn.execute(
        "SELECT fecha, misa, COUNT(*) AS n FROM registros GROUP BY fecha, misa"
    ).fetchall()
    return {(f["fecha"], f["misa"]): f["n"] for f in filas}


def insertar_registro(conn, nombre, apellido, celular, fecha, misa, excluir_id=None):
    """Devuelve 'ok', 'lleno' o 'duplicado'. Atomico: bloquea escritura mientras cuenta e inserta."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM registros WHERE fecha=? AND misa=? AND id IS NOT ?",
            (fecha, misa, excluir_id),
        ).fetchone()[0]
        if n >= CUPO_MAXIMO:
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


def es_admin():
    return session.get("admin") is True


def token_csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = token_csrf
app.jinja_env.globals["es_admin"] = es_admin
app.jinja_env.globals["MISAS"] = MISAS
app.jinja_env.globals["CUPO"] = CUPO_MAXIMO


def requiere_admin(f):
    from functools import wraps

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


# ---------- Publico ----------

def construir_meses(cuentas, domingos):
    disponibles = {d.isoformat() for d in domingos}
    primero = hoy().replace(day=1)
    ultimo = sumar_meses(hoy(), MESES_ADELANTE)
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
                celda = {"dia": d.day, "fecha": iso, "domingo": False}
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


def pagina_registro(datos=None, seleccionadas=()):
    conn = get_conn()
    cuentas = conteos(conn)
    conn.close()
    return render_template(
        "index.html",
        meses=construir_meses(cuentas, domingos_disponibles()),
        datos=datos or {"misa": "1030"},
        seleccionadas=set(seleccionadas),
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
    validas = {d.isoformat() for d in domingos_disponibles()}

    datos = {"nombre": nombre, "apellido": apellido,
             "celular": request.form.get("celular", ""), "misa": misa or "1030"}
    errores = []
    if not nombre:
        errores.append("Nombre es requerido.")
    if not apellido:
        errores.append("Apellido es requerido.")
    if not celular:
        errores.append("El celular debe tener 10 digitos.")
    if misa not in MISAS:
        errores.append("Elige un horario de misa.")
    if not fechas:
        errores.append("Elige al menos un domingo en el calendario.")
    if any(f not in validas for f in fechas):
        errores.append("Una de las fechas elegidas no esta disponible.")
    if errores:
        for e in errores:
            flash(e, "error")
        return pagina_registro(datos, fechas)

    conn = get_conn()
    ok, omitidos = [], []
    for f in fechas:
        resultado = insertar_registro(conn, nombre, apellido, celular, f, misa)
        if resultado == "ok":
            ok.append(fecha_corta(f))
        elif resultado == "lleno":
            omitidos.append(f"{fecha_corta(f)} (lleno)")
        else:
            omitidos.append(f"{fecha_corta(f)} (ya estabas inscrito)")
    conn.close()

    if ok:
        flash(f"Listo, {nombre}. Quedaste registrado en la misa de {MISAS[misa]} los domingos: "
              f"{', '.join(ok)}.", "success")
    if omitidos:
        flash(f"No se registro: {', '.join(omitidos)}.", "error")
    if not ok:
        return pagina_registro(datos, [])
    return redirect(url_for("index"))


@app.route("/turnos")
def turnos():
    conn = get_conn()
    filas = conn.execute(
        "SELECT fecha, misa, nombre, apellido FROM registros ORDER BY fecha, misa, creado, id"
    ).fetchall()
    conn.close()
    por_turno = {}
    for f in filas:
        por_turno.setdefault((f["fecha"], f["misa"]), []).append(f"{f['nombre']} {f['apellido']}")
    domingos = [
        {
            "fecha": d.isoformat(),
            "misas": [{"id": k, "etiqueta": v, "nombres": por_turno.get((d.isoformat(), k), [])}
                      for k, v in MISAS.items()],
        }
        for d in domingos_disponibles()
    ]
    return render_template("turnos.html", domingos=domingos, titulo_pagina="Turnos por domingo")


# ---------- Administrador ----------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    clave = os.environ.get("ADMIN_PASSWORD", "")
    if request.method == "POST":
        if not clave:
            flash("El acceso de administrador no esta configurado en el servidor.", "error")
        elif hmac.compare_digest(request.form.get("password", ""), clave):
            session.clear()
            session["admin"] = True
            session.permanent = True
            return redirect(url_for("admin"))
        else:
            flash("Contraseña incorrecta.", "error")
    return render_template("admin_login.html", titulo_pagina="Administrador")


@app.route("/admin/salir", methods=["POST"])
@requiere_admin
def admin_salir():
    session.clear()
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
    conn.close()
    return filas, fechas


@app.route("/admin")
@requiere_admin
def admin():
    fecha = request.args.get("fecha", "")
    filas, fechas_con_datos = registros_filtrados(fecha)
    todas = sorted({d.isoformat() for d in domingos_disponibles()} | set(fechas_con_datos))
    return render_template("admin.html", filas=filas, fechas=todas, filtro=fecha,
                           titulo_pagina="Administrar registros")


def datos_formulario_admin():
    errores = []
    nombre = limpiar_texto(request.form.get("nombre"))
    apellido = limpiar_texto(request.form.get("apellido"))
    celular = normalizar_celular(request.form.get("celular"))
    fecha = request.form.get("fecha", "")
    misa = request.form.get("misa", "")
    try:
        date.fromisoformat(fecha)
    except ValueError:
        errores.append("Fecha invalida.")
    if not nombre or not apellido:
        errores.append("Nombre y apellido son requeridos.")
    if not celular:
        errores.append("El celular debe tener 10 digitos.")
    if misa not in MISAS:
        errores.append("Horario de misa invalido.")
    return errores, (nombre, apellido, celular, fecha, misa)


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
            errores.append("Esa misa ya tiene 8 servidores." if res == "lleno"
                           else "Ese celular ya esta inscrito en esa misa.")
        for e in errores:
            flash(e, "error")
        d = dict(zip(("nombre", "apellido", "celular", "fecha", "misa"), datos))
        return render_template("admin_form.html", datos=d, domingos=domingos_disponibles(),
                               titulo="Añadir servidor", titulo_pagina="Añadir servidor")
    return render_template("admin_form.html", datos={"misa": "1030"}, domingos=domingos_disponibles(),
                           titulo="Añadir servidor", titulo_pagina="Añadir servidor")


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
                n = conn.execute(
                    "SELECT COUNT(*) FROM registros WHERE fecha=? AND misa=? AND id!=?",
                    (fecha, misa, id),
                ).fetchone()[0]
                if n >= CUPO_MAXIMO:
                    conn.rollback()
                    errores.append("Esa misa ya tiene 8 servidores.")
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
                errores.append("Ese celular ya esta inscrito en esa misa.")
            conn.close()
        for e in errores:
            flash(e, "error")
        d = dict(zip(("nombre", "apellido", "celular", "fecha", "misa"), datos))
        d["id"] = id
        datos_form = d
    else:
        datos_form = dict(fila)

    domingos = domingos_disponibles()
    if datos_form.get("fecha") and date.fromisoformat(datos_form["fecha"]) not in domingos:
        domingos = [date.fromisoformat(datos_form["fecha"])] + domingos
    return render_template("admin_form.html", datos=datos_form, domingos=domingos, editar=True,
                           titulo="Modificar servidor", titulo_pagina="Modificar servidor")


@app.route("/admin/eliminar/<int:id>", methods=["POST"])
@requiere_admin
def admin_eliminar(id):
    conn = get_conn()
    conn.execute("DELETE FROM registros WHERE id=?", (id,))
    conn.commit()
    conn.close()
    flash("Registro eliminado.", "success")
    return redirect(url_for("admin", fecha=request.form.get("fecha", "")))


@app.route("/admin/exportar")
@requiere_admin
def admin_exportar():
    filas, _ = registros_filtrados("")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Turnos de Parqueo"
    ws.append(["Fecha", "Misa", "Nombre", "Apellido", "Celular"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in filas:
        ws.append([r["fecha"], MISAS[r["misa"]], r["nombre"], r["apellido"], r["celular"]])
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
