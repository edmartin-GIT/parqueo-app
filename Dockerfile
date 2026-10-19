FROM python:3.12-slim

WORKDIR /app

COPY requirements-vps.txt .
RUN pip install --no-cache-dir -r requirements-vps.txt

COPY . .

ENV PARQUEO_DB_PATH=/app/data/parqueo.db
ENV COOKIE_SECURE=1

EXPOSE 8000

# Un solo proceso con hilos: SQLite maneja la escritura concurrente y la sesion de admin es consistente.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "app:app"]
