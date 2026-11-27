"""Webhook de WhatsApp Cloud API: validacion de firma, extraccion y guardado de eventos."""
import hashlib
import hmac
import json
import logging
import threading

from db import get_conn

logger = logging.getLogger(__name__)


def firma_valida(body_bytes, header, secreto):
    """Valida X-Hub-Signature-256 (`sha256=<hex>`) contra el cuerpo crudo."""
    if not secreto or not header or not isinstance(header, str):
        return False
    if not header.startswith("sha256="):
        return False
    recibida = header[len("sha256="):].strip()
    if not recibida:
        return False
    if isinstance(secreto, str):
        secreto = secreto.encode("utf-8")
    esperada = hmac.new(secreto, body_bytes or b"", hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperada, recibida.lower())


def _lista(valor):
    return valor if isinstance(valor, list) else []


def _dict(valor):
    return valor if isinstance(valor, dict) else {}


def extraer_eventos(payload):
    """Devuelve una lista de eventos (status / message) del payload; nunca lanza."""
    eventos = []
    try:
        for entry in _lista(_dict(payload).get("entry")):
            for change in _lista(_dict(entry).get("changes")):
                value = _dict(_dict(change).get("value"))
                for st in _lista(value.get("statuses")):
                    st = _dict(st)
                    ev = {
                        "tipo": "status",
                        "wamid": st.get("id"),
                        "status": st.get("status"),
                        "timestamp": st.get("timestamp"),
                        "recipient_id": st.get("recipient_id"),
                    }
                    if st.get("status") == "failed":
                        errores = _lista(st.get("errors"))
                        if errores:
                            err = _dict(errores[0])
                            ev["error_code"] = err.get("code")
                            ev["error_title"] = err.get("title")
                            ev["error_details"] = _dict(err.get("error_data")).get("details")
                    eventos.append(ev)
                for msg in _lista(value.get("messages")):
                    msg = _dict(msg)
                    ev = {
                        "tipo": "message",
                        "from_number": msg.get("from"),
                        "wamid": msg.get("id"),
                        "msg_type": msg.get("type"),
                        "timestamp": msg.get("timestamp"),
                    }
                    if msg.get("type") == "text":
                        ev["text_body"] = _dict(msg.get("text")).get("body")
                    eventos.append(ev)
    except Exception:  # defensivo: un payload raro nunca debe reventar
        logger.exception("Error extrayendo eventos del webhook de WhatsApp")
    return eventos


def _texto(v):
    """SQLite solo acepta tipos escalares; convierte lo raro a texto."""
    if v is None or isinstance(v, str):
        return v
    return str(v)


def _entero(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def guardar_eventos(eventos):
    """Inserta los eventos en whatsapp_events y avisa en el log de cada failed."""
    if not eventos:
        return
    conn = get_conn()
    try:
        for ev in eventos:
            conn.execute(
                """INSERT INTO whatsapp_events
                   (tipo, wamid, status, timestamp, recipient_id, error_code,
                    error_title, error_details, from_number, msg_type, text_body)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ev.get("tipo"), _texto(ev.get("wamid")), _texto(ev.get("status")),
                    _texto(ev.get("timestamp")), _texto(ev.get("recipient_id")),
                    _entero(ev.get("error_code")), _texto(ev.get("error_title")),
                    _texto(ev.get("error_details")), _texto(ev.get("from_number")),
                    _texto(ev.get("msg_type")), _texto(ev.get("text_body")),
                ),
            )
            if ev.get("tipo") == "status" and ev.get("status") == "failed":
                logger.warning(
                    "WhatsApp failed: wamid=%s recipient=%s codigo=%s titulo=%s",
                    ev.get("wamid"), ev.get("recipient_id"),
                    ev.get("error_code"), ev.get("error_title"),
                )
        conn.commit()
    finally:
        conn.close()


def procesar(body_bytes):
    """Version sincrona: parsea el JSON y guarda los eventos."""
    payload = json.loads(body_bytes.decode("utf-8"))
    guardar_eventos(extraer_eventos(payload))


def _tarea(body_bytes):
    try:
        procesar(body_bytes)
    except Exception:
        logger.exception("Error procesando webhook de WhatsApp")


def procesar_en_segundo_plano(body_bytes):
    """Lanza `procesar` en un hilo daemon y devuelve el hilo."""
    hilo = threading.Thread(target=_tarea, args=(body_bytes,), daemon=True)
    hilo.start()
    return hilo
