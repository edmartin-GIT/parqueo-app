import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("PARQUEO_DB_PATH", Path(__file__).parent / "parqueo.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

CUPO_MAXIMO = 8
MESES_ADELANTE = 3

MISAS = {
    "1030": "10:30 AM",
    "1330": "1:30 PM",
    "1900": "7:00 PM",
}


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS registros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            apellido TEXT NOT NULL,
            celular TEXT NOT NULL,
            fecha TEXT NOT NULL,
            misa TEXT NOT NULL CHECK(misa IN ('1030', '1330', '1900')),
            creado TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fecha, misa, celular)
        );
        CREATE INDEX IF NOT EXISTS idx_registros_turno ON registros(fecha, misa);
        """
    )
    conn.commit()
    conn.close()
