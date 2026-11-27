import hashlib
import hmac
import json
import os
import sys
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="parqueo_test_")
os.environ["PARQUEO_DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["WHATSAPP_VERIFY_TOKEN"] = "token-de-prueba"
os.environ["WHATSAPP_APP_SECRET"] = "secreto-de-prueba"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_mod  # noqa: E402
import webhook_whatsapp as wh  # noqa: E402
from db import get_conn  # noqa: E402

SECRETO = "secreto-de-prueba"

PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [{
        "id": "123",
        "changes": [{
            "field": "messages",
            "value": {
                "messaging_product": "whatsapp",
                "statuses": [
                    {"id": "wamid.OK", "status": "delivered", "timestamp": "1700000000",
                     "recipient_id": "15551234567"},
                    {"id": "wamid.FAIL", "status": "failed", "timestamp": "1700000001",
                     "recipient_id": "15557654321",
                     "errors": [{"code": 131042, "title": "Business eligibility payment issue",
                                 "error_data": {"details": "Revisar metodo de pago"}}]},
                ],
                "messages": [
                    {"from": "15551234567", "id": "wamid.IN", "timestamp": "1700000002",
                     "type": "text", "text": {"body": "Hola"}},
                ],
            },
        }],
    }],
}


def firmar(body, secreto=SECRETO):
    return "sha256=" + hmac.new(secreto.encode(), body, hashlib.sha256).hexdigest()


def contar():
    conn = get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM whatsapp_events").fetchone()[0]
    finally:
        conn.close()


class WebhookTest(unittest.TestCase):
    def setUp(self):
        os.environ["WHATSAPP_VERIFY_TOKEN"] = "token-de-prueba"
        os.environ["WHATSAPP_APP_SECRET"] = SECRETO
        conn = get_conn()
        conn.execute("DELETE FROM whatsapp_events")
        conn.commit()
        conn.close()
        self.client = app_mod.app.test_client()
        # Captura el hilo para poder esperarlo
        self.hilos = []
        self._orig = app_mod.procesar_en_segundo_plano

        def espia(body):
            h = self._orig(body)
            self.hilos.append(h)
            return h
        app_mod.procesar_en_segundo_plano = espia
        self.addCleanup(setattr, app_mod, "procesar_en_segundo_plano", self._orig)

    def esperar(self):
        for h in self.hilos:
            h.join(5)

    def test_get_verificacion_ok(self):
        r = self.client.get("/webhooks/whatsapp", query_string={
            "hub.mode": "subscribe", "hub.verify_token": "token-de-prueba",
            "hub.challenge": "12345"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_data(as_text=True), "12345")

    def test_get_token_incorrecto(self):
        r = self.client.get("/webhooks/whatsapp", query_string={
            "hub.mode": "subscribe", "hub.verify_token": "malo", "hub.challenge": "1"})
        self.assertEqual(r.status_code, 403)

    def test_get_mode_distinto(self):
        r = self.client.get("/webhooks/whatsapp", query_string={
            "hub.mode": "otro", "hub.verify_token": "token-de-prueba", "hub.challenge": "1"})
        self.assertEqual(r.status_code, 403)

    def test_get_sin_env_configurada(self):
        os.environ["WHATSAPP_VERIFY_TOKEN"] = ""
        r = self.client.get("/webhooks/whatsapp", query_string={
            "hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"})
        self.assertEqual(r.status_code, 403)

    def test_post_firmado_guarda_eventos(self):
        body = json.dumps(PAYLOAD).encode()
        r = self.client.post("/webhooks/whatsapp", data=body,
                             headers={"X-Hub-Signature-256": firmar(body),
                                      "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 200)
        self.esperar()
        conn = get_conn()
        filas = conn.execute("SELECT * FROM whatsapp_events ORDER BY id").fetchall()
        conn.close()
        self.assertEqual(len(filas), 3)
        failed = [f for f in filas if f["status"] == "failed"][0]
        self.assertEqual(failed["error_code"], 131042)
        self.assertEqual(failed["wamid"], "wamid.FAIL")
        msg = [f for f in filas if f["tipo"] == "message"][0]
        self.assertEqual(msg["text_body"], "Hola")
        self.assertEqual(msg["from_number"], "15551234567")

    def test_procesar_sincrono(self):
        wh.procesar(json.dumps(PAYLOAD).encode())
        self.assertEqual(contar(), 3)

    def test_post_firma_incorrecta(self):
        body = json.dumps(PAYLOAD).encode()
        r = self.client.post("/webhooks/whatsapp", data=body,
                             headers={"X-Hub-Signature-256": firmar(body, "otro")})
        self.assertEqual(r.status_code, 401)
        self.esperar()
        self.assertEqual(contar(), 0)

    def test_post_sin_header(self):
        body = json.dumps(PAYLOAD).encode()
        r = self.client.post("/webhooks/whatsapp", data=body)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(contar(), 0)

    def test_post_demasiado_grande(self):
        body = b"x" * (1024 * 1024 + 1)
        r = self.client.post("/webhooks/whatsapp", data=body,
                             headers={"X-Hub-Signature-256": firmar(body)})
        self.assertEqual(r.status_code, 413)

    def test_extraer_eventos_malformado(self):
        for p in (None, {}, [], "x", {"entry": "x"}, {"entry": [None, 1]},
                  {"entry": [{"changes": [{"value": {"statuses": "x", "messages": [1, None]}}]}]},
                  {"entry": [{"changes": [{"value": {"statuses": [{"status": "failed", "errors": "x"}]}}]}]}):
            wh.extraer_eventos(p)
        self.assertEqual(wh.extraer_eventos({}), [])
        self.assertEqual(wh.extraer_eventos(None), [])
        self.assertEqual(wh.extraer_eventos({"entry": "x"}), [])

    def test_firma_valida_casos(self):
        self.assertFalse(wh.firma_valida(b"a", "", SECRETO))
        self.assertFalse(wh.firma_valida(b"a", "sha256=abc", ""))
        self.assertFalse(wh.firma_valida(b"a", "md5=abc", SECRETO))
        self.assertTrue(wh.firma_valida(b"a", firmar(b"a"), SECRETO))


if __name__ == "__main__":
    unittest.main()
