import os
import sys
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="parqueo_test_")
os.environ["PARQUEO_DB_PATH"] = os.path.join(_TMP, "test_busqueda.db")
os.environ["ADMIN_PASSWORD"] = "clave-de-prueba"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_mod  # noqa: E402
from app import app, normalizar_busqueda  # noqa: E402
from db import get_conn, init_db  # noqa: E402

REGISTROS = [
    ("José", "Pérez", "3055550001", "2026-12-06"),
    ("Jose", "Perez", "3055550002", "2026-12-13"),
    ("Maria", "Lopez", "3055550003", "2026-12-20"),
]


class AdminBusquedaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        conn = get_conn()
        conn.execute("DELETE FROM registros")
        for n, a, c, f in REGISTROS:
            conn.execute(
                "INSERT INTO registros (nombre, apellido, celular, fecha, misa) VALUES (?,?,?,?,'1030')",
                (n, a, c, f),
            )
        conn.commit()
        conn.close()

    def setUp(self):
        self.client = app.test_client()
        r = self.client.post("/admin/login", data={"password": "clave-de-prueba"})
        self.assertEqual(r.status_code, 302)

    def pagina(self, **params):
        r = self.client.get("/admin", query_string=params)
        self.assertEqual(r.status_code, 200)
        return r.get_data(as_text=True)

    def test_normalizar(self):
        self.assertEqual(normalizar_busqueda("  JOSÉ   Pérez "), "jose perez")
        self.assertEqual(normalizar_busqueda(None), "")

    def test_insensible_a_acentos_y_mayusculas(self):
        html = self.pagina(q="JOSE")
        self.assertIn("3055550001", html)
        self.assertIn("3055550002", html)
        self.assertNotIn("3055550003", html)

    def test_orden_apellido_nombre(self):
        html = self.pagina(q="perez jose")
        self.assertIn("3055550001", html)
        self.assertIn("3055550002", html)
        self.assertNotIn("3055550003", html)

    def test_por_celular(self):
        html = self.pagina(q="5550003")
        self.assertIn("3055550003", html)
        self.assertNotIn("3055550001", html)

    def test_combina_con_fecha(self):
        html = self.pagina(q="jose", fecha="2026-12-13")
        self.assertIn("3055550002", html)
        self.assertNotIn("3055550001", html)

    def test_sin_coincidencias(self):
        html = self.pagina(q="zzzz")
        self.assertIn("No se encontraron", html)

    def test_asistencia_conserva_q(self):
        self.pagina()
        with self.client.session_transaction() as s:
            csrf = s["csrf"]
        conn = get_conn()
        rid = conn.execute("SELECT id FROM registros WHERE celular='3055550001'").fetchone()["id"]
        conn.close()
        r = self.client.post(f"/admin/asistencia/{rid}",
                             data={"csrf": csrf, "fecha": "", "q": "jose", "asistio": "1"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("q=jose", r.headers["Location"])


if __name__ == "__main__":
    unittest.main()
