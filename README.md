# Turnos de Parqueo - La Guadalupe Doral

Sitio web publico para que los servidores se inscriban a los turnos de parqueo de las misas dominicales (10:00 AM, 1:30 PM y 7:00 PM). Maximo 8 servidores por misa y calendario con los domingos de los proximos 3 meses.

- **Publico** (cualquiera con el enlace): registro con nombre, apellido, celular y horario de misa; calendario para marcar los domingos; lista de turnos por domingo (sin celulares).
- **Administrador** (`/admin`, protegido con contraseña): ver, añadir, modificar y borrar registros, y exportar a Excel.

## Instalacion local

```bash
pip install -r requirements.txt
ADMIN_PASSWORD=una-clave python app.py
```

Queda en `http://localhost:5002`. La base SQLite (`parqueo.db`) se crea sola en el primer arranque.

## Estructura

- `app.py` - rutas y logica (Flask). El cupo de 8 se verifica dentro de una transaccion SQLite, asi dos personas no pueden tomar el ultimo lugar a la vez.
- `db.py` - base SQLite, horarios de misa, cupo y meses de anticipacion. Respeta `PARQUEO_DB_PATH`.
- `templates/`, `static/` - vistas y CSS.
- `Dockerfile` - imagen de produccion (Gunicorn, puerto 8000).

## Variables de entorno

| Variable | Uso |
|---|---|
| `ADMIN_PASSWORD` | Contraseña del panel `/admin`. Sin ella el acceso de administrador queda deshabilitado. |
| `PARQUEO_DB_PATH` | Ruta de la base SQLite (en Docker: `/app/data/parqueo.db`). |
| `SECRET_KEY` | Opcional. Si falta, se genera una y se guarda junto a la base de datos. |
| `COOKIE_SECURE` | `1` en produccion con HTTPS (el Dockerfile ya lo activa). |

## Despliegue en el VPS de curiositylogic.cloud (Docker + Traefik)

Igual que `rifa-app`: un servicio mas en `/root/docker-compose.yml`.

1. Clonar el repo:
   ```bash
   git clone https://github.com/edmartin-GIT/parqueo-app.git /var/www/parqueo-app
   ```
2. Agregar el servicio al compose (junto a traefik, n8n, orqagent, rifa-app):
   ```yaml
     parqueo-app:
       build: /var/www/parqueo-app
       restart: always
       environment:
         - ADMIN_PASSWORD=${PARQUEO_ADMIN_PASSWORD}
       volumes:
         - parqueo_data:/app/data
       labels:
         - traefik.enable=true
         - traefik.http.routers.parqueo-app.rule=Host(`parqueo.curiositylogic.cloud`)
         - traefik.http.routers.parqueo-app.tls=true
         - traefik.http.routers.parqueo-app.entrypoints=web,websecure
         - traefik.http.routers.parqueo-app.tls.certresolver=mytlschallenge
         - traefik.http.services.parqueo-app.loadbalancer.server.port=8000
   ```
   Agregar `parqueo_data:` a la seccion `volumes:` y definir `PARQUEO_ADMIN_PASSWORD=...` en `/root/.env`.
3. Crear el registro DNS tipo A: `parqueo.curiositylogic.cloud` -> IP del VPS.
4. Levantar:
   ```bash
   cd /root && docker compose up -d --build parqueo-app
   ```

Actualizaciones: `cd /var/www/parqueo-app && git pull && cd /root && docker compose up -d --build parqueo-app`.

Respaldo de la base: `docker run --rm -v parqueo_data:/data -v $(pwd):/backup alpine tar czf /backup/parqueo_data_backup.tar.gz /data`.
