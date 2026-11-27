import os
import sys
import tempfile
import unittest
from datetime import date

_TMP = tempfile.mkdtemp(prefix="parqueo_test_")
os.environ.setdefault("PARQUEO_DB_PATH", os.path.join(_TMP, "test_ranking.db"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ranking  # noqa: E402
from ranking import calcular_ranking, inicio_reto  # noqa: E402

INICIO = date(2026, 12, 1)


def fila(fecha, celular="3055550001", nombre="Ana", apellido="Lopez", asistio=1, misa="1030"):
    return {"fecha": fecha, "misa": misa, "nombre": nombre, "apellido": apellido,
            "celular": celular, "asistio": asistio}


def por_nombre(res):
    return {x["nombre"]: x for x in res}


class RankingRetoTest(unittest.TestCase):
    def test_antes_del_reto_todos_en_cero(self):
        filas = [
            fila("2026-10-04"), fila("2026-11-01"), fila("2026-12-06"),
            fila("2026-12-13", celular="3055550002", nombre="Beto", apellido="Perez", asistio=None),
        ]
        res = calcular_ranking(filas, date(2026, 11, 26), inicio=INICIO)
        self.assertEqual(len(res), 2)
        for x in res:
            self.assertEqual((x["estrellas"], x["servicios"], x["ausencias"]), (0, 0, 0))
        self.assertEqual([x["nombre"] for x in res], ["Ana", "Beto"])

    def test_servicio_previo_no_cuenta_y_primero_vale_tres(self):
        filas = [fila("2026-11-29"), fila("2026-12-06")]
        res = calcular_ranking(filas, date(2026, 12, 10), inicio=INICIO)
        self.assertEqual(res[0]["estrellas"], 3)
        self.assertEqual(res[0]["servicios"], 1)

    def test_ausencia_no_marcada(self):
        res = calcular_ranking([fila("2026-12-06", asistio=None)], date(2026, 12, 10), inicio=INICIO)
        self.assertEqual(res[0]["estrellas"], -1)
        self.assertEqual(res[0]["ausencias"], 1)

    def test_misa_especial(self):
        filas = [fila("2026-12-06"), fila("2026-12-08", misa="E5")]
        res = calcular_ranking(filas, date(2026, 12, 10), inicio=INICIO)
        self.assertEqual(res[0]["estrellas"], 5)

    def test_bono_mes_completo(self):
        filas = [fila(f"2026-12-{d:02d}") for d in (6, 13, 20, 27)]
        antes = calcular_ranking(filas, date(2026, 12, 31), inicio=INICIO)
        self.assertEqual(antes[0]["estrellas"], 6)
        self.assertEqual(antes[0]["meses_perfectos"], [])
        despues = calcular_ranking(filas, date(2027, 1, 1), inicio=INICIO)
        self.assertEqual(despues[0]["estrellas"], 11)
        self.assertEqual(despues[0]["meses_perfectos"], [(2026, 12)])

    def test_posicion_con_empates(self):
        filas = [fila("2026-12-06"), fila("2026-12-06", celular="3055550002", nombre="Beto")]
        res = calcular_ranking(filas, date(2026, 12, 10), inicio=INICIO)
        self.assertEqual([x["posicion"] for x in res], [1, 1])


class InicioRetoTest(unittest.TestCase):
    def setUp(self):
        self._previo = os.environ.pop("RANKING_INICIO", None)

    def tearDown(self):
        os.environ.pop("RANKING_INICIO", None)
        if self._previo is not None:
            os.environ["RANKING_INICIO"] = self._previo

    def test_default(self):
        self.assertEqual(inicio_reto(), ranking.INICIO_RETO)

    def test_env_valida(self):
        os.environ["RANKING_INICIO"] = "2026-01-15"
        self.assertEqual(inicio_reto(), date(2026, 1, 15))

    def test_env_invalida(self):
        os.environ["RANKING_INICIO"] = "no-es-fecha"
        self.assertEqual(inicio_reto(), ranking.INICIO_RETO)


class PaginaRetoTest(unittest.TestCase):
    def setUp(self):
        import app as app_mod
        self.app_mod = app_mod
        self.client = app_mod.app.test_client()
        self._previo = os.environ.pop("RANKING_INICIO", None)

    def tearDown(self):
        os.environ.pop("RANKING_INICIO", None)
        if self._previo is not None:
            os.environ["RANKING_INICIO"] = self._previo

    def test_reto_no_iniciado(self):
        os.environ["RANKING_INICIO"] = "2999-01-01"
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("El reto comienza", r.get_data(as_text=True))

    def test_reto_iniciado(self):
        os.environ["RANKING_INICIO"] = "2020-01-01"
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("El reto comienza", r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
