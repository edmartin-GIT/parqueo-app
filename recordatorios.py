"""Recordatorios de WhatsApp para los servidores inscritos el dia siguiente."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import whatsapp
from db import MISAS, get_conn

ZONA = ZoneInfo("America/New_York")

MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def fecha_es(iso):
    d = date.fromisoformat(iso)
    return f"{DIAS_ES[d.weekday()]} {d.day} de {MESES_ES[d.month - 1]}"


def _hora12(h, m):
    return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def _hm_de_etiqueta(etiqueta):
    hm, ampm = etiqueta.split()
    h, m = (int(x) for x in hm.split(":"))
    h = h % 12 + (12 if ampm.upper() == "PM" else 0)
    return h, m


def horas_turno(conn, misa):
    """(hora de misa, hora de llegada 30 min antes) en texto 12h, o None si el turno no existe."""
    if misa in MISAS:
        h, m = _hm_de_etiqueta(MISAS[misa])
    elif misa.startswith("E") and misa[1:].isdigit():
        e = conn.execute("SELECT hora FROM misas_especiales WHERE id=?", (int(misa[1:]),)).fetchone()
        if not e:
            return None
        h, m = (int(x) for x in e["hora"].split(":"))
    else:
        return None
    total = (h * 60 + m - 30) % 1440
    return _hora12(h, m), _hora12(total // 60, total % 60)


def parametros_plantilla(nombre, fecha, hora_misa, hora_llegada):
    return [nombre, fecha_es(fecha), hora_misa, hora_llegada]


def estado_recordatorios(conn, fecha):
    """Registros de la fecha con su recordatorio (si existe), para el panel de admin."""
    return conn.execute(
        """
        SELECT r.*, rc.estado AS rec_estado, rc.enviado_en AS rec_enviado_en, rc.error AS rec_error
        FROM registros r
        LEFT JOIN recordatorios rc ON rc.registro_id = r.id
             AND rc.tipo = CASE WHEN r.misa LIKE 'E%' THEN 'especial' ELSE 'misa' END
             AND rc.fecha = r.fecha
        WHERE r.fecha = ?
        ORDER BY r.misa, r.creado, r.id
        """,
        (fecha,),
    ).fetchall()


def enviar_recordatorios(fecha_objetivo, forzar_simulado=False):
    """Envia (o simula) el recordatorio a los inscritos de fecha_objetivo (YYYY-MM-DD).
    Idempotente: no repite los ya 'enviado'; reintenta 'error', 'simulado' y 'sin_telefono'."""
    simulado = forzar_simulado or not whatsapp.configurado()
    resumen = {"fecha": fecha_objetivo, "simulado": simulado, "total": 0, "enviados": 0,
               "simulados": 0, "errores": 0, "sin_telefono": 0, "ya_enviados": 0, "omitidos": 0}
    conn = get_conn()
    try:
        filas = conn.execute(
            "SELECT * FROM registros WHERE fecha=? AND acepta_whatsapp=1 ORDER BY misa, id",
            (fecha_objetivo,),
        ).fetchall()
        resumen["total"] = len(filas)
        for r in filas:
            tipo = "especial" if r["misa"].startswith("E") else "misa"
            previo = conn.execute(
                "SELECT estado, fecha, misa FROM recordatorios WHERE registro_id=? AND tipo=?",
                (r["id"], tipo),
            ).fetchone()
            if (previo and previo["estado"] == "enviado" and previo["fecha"] == fecha_objetivo
                    and previo["misa"] == r["misa"]):
                resumen["ya_enviados"] += 1
                continue
            horas = horas_turno(conn, r["misa"])
            if horas is None:
                resumen["omitidos"] += 1
                continue
            tel = whatsapp.normalizar_telefono(r["celular"])
            mid = error = None
            if tel is None:
                estado = "sin_telefono"
                resumen["sin_telefono"] += 1
            elif simulado:
                estado = "simulado"
                resumen["simulados"] += 1
            else:
                ok, mid, error = whatsapp.enviar_plantilla(
                    tel, parametros_plantilla(r["nombre"], fecha_objetivo, *horas))
                estado = "enviado" if ok else "error"
                resumen["enviados" if ok else "errores"] += 1
            ahora = datetime.now(ZONA).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """
                INSERT INTO recordatorios (registro_id, tipo, fecha, misa, telefono, estado,
                                           wa_message_id, error, enviado_en)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(registro_id, tipo) DO UPDATE SET
                    fecha=excluded.fecha, misa=excluded.misa, telefono=excluded.telefono,
                    estado=excluded.estado, wa_message_id=excluded.wa_message_id,
                    error=excluded.error, enviado_en=excluded.enviado_en
                """,
                (r["id"], tipo, fecha_objetivo, r["misa"], tel, estado, mid, error, ahora),
            )
            conn.commit()
    finally:
        conn.close()
    return resumen
