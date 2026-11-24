"""Cliente minimo de la API de WhatsApp Cloud (Meta) para plantillas aprobadas."""
import json
import os
import re
import urllib.error
import urllib.request


def _cfg(nombre, defecto=""):
    return os.environ.get(nombre, defecto).strip()


def configurado():
    return bool(_cfg("WHATSAPP_TOKEN") and _cfg("WHATSAPP_PHONE_NUMBER_ID"))


def normalizar_telefono(celular):
    """Devuelve el telefono en formato E.164 sin '+' (ej. 13055550123) o None si no es valido."""
    digitos = re.sub(r"\D", "", celular or "")
    if len(digitos) == 10:
        return "1" + digitos
    if len(digitos) == 11 and digitos.startswith("1"):
        return digitos
    return None


def enviar_plantilla(telefono, parametros):
    """Envia la plantilla configurada. Devuelve (ok, message_id | None, error | None)."""
    base = _cfg("WHATSAPP_API_URL", "https://graph.facebook.com/v21.0").rstrip("/")
    url = f"{base}/{_cfg('WHATSAPP_PHONE_NUMBER_ID')}/messages"
    cuerpo = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "template",
        "template": {
            "name": _cfg("WHATSAPP_TEMPLATE", "recordatorio_parqueo"),
            "language": {"code": _cfg("WHATSAPP_TEMPLATE_LANG", "es")},
            "components": [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)} for p in parametros],
            }],
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(cuerpo).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {_cfg('WHATSAPP_TOKEN')}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            datos = json.loads(resp.read().decode("utf-8") or "{}")
        mid = (datos.get("messages") or [{}])[0].get("id")
        if not mid:
            return False, None, "Respuesta sin id de mensaje"
        return True, mid, None
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8"))
            msg = (err.get("error") or {}).get("message") or f"HTTP {e.code}"
        except Exception:
            msg = f"HTTP {e.code}"
        return False, None, msg[:300]
    except Exception as e:  # red, timeout, JSON invalido
        return False, None, f"{type(e).__name__}: {e}"[:300]
