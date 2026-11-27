"""Ranking de servidores del parqueo (reto de estrellas).

Reglas:
1. Primer servicio al que asiste un servidor: 3 estrellas en total.
2. Cada servicio al que asiste despues del primero: 1 estrella (2 si es misa especial).
3. Cada servicio registrado al que no asiste: -1 estrella.
4. Por cada mes en que asiste a todos los domingos del mes: +5 estrellas,
   acreditadas el primer dia del mes siguiente.

Solo cuentan los servicios hasta hoy. Un servicio de hoy cuenta cuando ya esta marcado
(asistio / no asistio); los de dias anteriores sin marcar cuentan como no asistidos
(ASUMIR_ASISTENCIA=False).
"""
import calendar
import re
from datetime import date

ESTRELLAS_PRIMER_SERVICIO = 3
ESTRELLAS_POR_SERVICIO = 1
PENALIZACION_AUSENCIA = 1
BONO_MES_COMPLETO = 5
ASUMIR_ASISTENCIA = False
ESTRELLAS_MISA_ESPECIAL = 2


def domingos_del_mes(anio, mes):
    ultimo = calendar.monthrange(anio, mes)[1]
    return [date(anio, mes, d) for d in range(1, ultimo + 1) if date(anio, mes, d).weekday() == 6]


def _primer_dia_mes_siguiente(anio, mes):
    return date(anio + (mes == 12), mes % 12 + 1, 1)


def nombre_publico(nombre, apellido):
    """Nombre y la inicial del apellido: no se publica el apellido completo."""
    inicial = (apellido or "").strip()[:1].upper()
    return f"{(nombre or '').strip()} {inicial}.".strip() if inicial else (nombre or "").strip()


def calcular_ranking(filas, hoy, asumir_asistencia=ASUMIR_ASISTENCIA):
    """filas: iterable de dicts con fecha (ISO), nombre, apellido, celular, asistio (None/0/1)."""
    personas = {}
    for r in filas:
        f = date.fromisoformat(r["fecha"])
        if f > hoy:
            continue
        asistio = r.get("asistio")
        if asistio is None:
            if f == hoy:
                continue
            asistio = 1 if asumir_asistencia else 0
        clave = re.sub(r"\D", "", r["celular"] or "") or f"{r['nombre']}|{r['apellido']}".casefold()
        p = personas.setdefault(clave, {"servicios": [], "ultimo": date.min, "nombre": "", "apellido": ""})
        p["servicios"].append((f, bool(asistio), str(r.get("misa") or "").startswith("E")))
        if f >= p["ultimo"]:
            p["ultimo"], p["nombre"], p["apellido"] = f, r["nombre"], r["apellido"]

    resultado = []
    for p in personas.values():
        asist = sorted((f, esp) for f, ok, esp in p["servicios"] if ok)
        asistidos = [f for f, _ in asist]
        ausencias = sum(1 for _, ok, _e in p["servicios"] if not ok)

        estrellas = 0
        if asist:
            estrellas += ESTRELLAS_PRIMER_SERVICIO
            estrellas += sum(ESTRELLAS_MISA_ESPECIAL if esp else ESTRELLAS_POR_SERVICIO for _, esp in asist[1:])
        estrellas -= ausencias * PENALIZACION_AUSENCIA

        domingos_asistidos = {f for f in asistidos if f.weekday() == 6}
        meses_perfectos = []
        for anio, mes in sorted({(f.year, f.month) for f in domingos_asistidos}):
            if _primer_dia_mes_siguiente(anio, mes) > hoy:
                continue  # el bono se acredita al comenzar el mes siguiente
            if all(d in domingos_asistidos for d in domingos_del_mes(anio, mes)):
                meses_perfectos.append((anio, mes))
        estrellas += BONO_MES_COMPLETO * len(meses_perfectos)

        resultado.append({
            "nombre": p["nombre"], "apellido": p["apellido"],
            "publico": nombre_publico(p["nombre"], p["apellido"]),
            "estrellas": estrellas, "servicios": len(asistidos), "ausencias": ausencias,
            "meses_perfectos": meses_perfectos,
        })

    resultado.sort(key=lambda x: (-x["estrellas"], -x["servicios"], x["publico"].casefold()))
    posicion = 0
    previo = None
    for i, x in enumerate(resultado, start=1):
        clave = (x["estrellas"], x["servicios"])
        if clave != previo:
            posicion, previo = i, clave
        x["posicion"] = posicion
    return resultado
