# Turnos de Parqueo - La Guadalupe Doral

Sitio web publico para que los servidores se inscriban a los turnos de parqueo de las misas dominicales (10:00 AM, 1:30 PM y 7:00 PM). Maximo 12 servidores por misa y calendario con los domingos de los proximos 3 meses.

- **Publico** (cualquiera con el enlace): registro con nombre, apellido, celular y horario de misa; calendario para marcar los domingos; lista de turnos por domingo (sin celulares).
- **Administrador** (`/admin`, protegido con contraseña): ver, añadir, modificar y borrar registros, y exportar a Excel.

## Instalacion local

```bash
pip install -r requirements.txt
ADMIN_PASSWORD=una-clave python app.py
```

Queda en `http://localhost:5002`. La base SQLite (`parqueo.db`) se crea sola en el primer arranque.

## Estructura

- `app.py` - rutas y logica (Flask). El cupo de 12 se verifica dentro de una transaccion SQLite, asi dos personas no pueden tomar el ultimo lugar a la vez.
- `db.py` - base SQLite, horarios de misa, cupo y meses de anticipacion. Respeta `PARQUEO_DB_PATH`.
- `templates/`, `static/` - vistas y CSS.
- `Dockerfile` - imagen de produccion (Gunicorn, puerto 8000).

## Variables de entorno

| Variable | Uso |
|---|---|
| `ADMIN_PASSWORD` | Contraseña del panel `/admin`. Sin ella el acceso de administrador queda deshabilitado. |
| `PARQUEO_DB_PATH` | Ruta de la base SQLite (en Docker: `/app/data/parqueo.db`). |
| `SECRET_KEY` | Opcional. Si falta, se genera una y se guarda junto a la base de datos. |
| `RANKING_INICIO` | Opcional. Fecha ISO (`AAAA-MM-DD`) de inicio del reto de estrellas. Por defecto `2026-12-01`. |
| `COOKIE_SECURE` | `1` en produccion con HTTPS (el Dockerfile ya lo activa). |

## Reto de estrellas (ranking)

El reto comienza el 1 de diciembre de 2026: antes de esa fecha todos los servidores aparecen con 0 estrellas, y desde entonces solo cuentan los servicios con fecha igual o posterior al inicio (el primer servicio asistido dentro del reto vale 3 estrellas). Se puede cambiar con la variable opcional `RANKING_INICIO`.

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
         - traefik.http.routers.parqueo-app.rule=Host(`parqueo.guadalupedoral.app`)
         - traefik.http.routers.parqueo-app.tls=true
         - traefik.http.routers.parqueo-app.entrypoints=web,websecure
         - traefik.http.routers.parqueo-app.tls.certresolver=mytlschallenge
         - traefik.http.services.parqueo-app.loadbalancer.server.port=8000
   ```
   Agregar `parqueo_data:` a la seccion `volumes:` y definir `PARQUEO_ADMIN_PASSWORD=...` en `/root/.env`.
3. Crear el registro DNS tipo A: `parqueo.guadalupedoral.app` -> IP del VPS.
4. Levantar:
   ```bash
   cd /root && docker compose up -d --build parqueo-app
   ```

Actualizaciones: `cd /var/www/parqueo-app && git pull && cd /root && docker compose up -d --build parqueo-app`.

Respaldo de la base: `docker run --rm -v parqueo_data:/data -v $(pwd):/backup alpine tar czf /backup/parqueo_data_backup.tar.gz /data`.

## Recordatorios por WhatsApp

Cada servidor recibe un mensaje de WhatsApp el dia antes de su turno (domingos y misas especiales) usando la API oficial de WhatsApp Cloud de Meta con una plantilla pre-aprobada. Solo se envia a quienes dejaron marcada la casilla "Acepto recibir un recordatorio por WhatsApp" (las inscripciones anteriores a esta funcion quedan como aceptadas).

### Configuracion en Meta

1. Crear una cuenta de **Meta Business** (business.facebook.com).
2. En developers.facebook.com crear una app de tipo Business y agregar el producto **WhatsApp**.
3. Agregar el numero de telefono que enviara los mensajes. Notas:
   - Un numero de **Google Voice** sirve si se registra en la Cloud API (no en la app de WhatsApp) y no esta ya registrado en WhatsApp.
   - Si el codigo por SMS no llega, elegir la verificacion por **llamada de voz**.
   - Google Voice puede reclamar numeros inactivos: hacer una llamada o enviar un texto desde el numero cada un par de meses.
4. Crear un **usuario del sistema** (System User) en Business Settings, asignarle la app y generar un **token permanente** con el permiso `whatsapp_business_messaging`.
5. Anotar el **Phone Number ID** (en WhatsApp > API Setup).
6. Crear y enviar a aprobacion la plantilla (categoria **UTILITY**, idioma **Spanish**, nombre `recordatorio_parqueo`) con este cuerpo:

   ```
   Hola {{1}}, te recordamos tu turno de servicio de parqueo mañana {{2}} para la misa de {{3}}. Por favor llega a las {{4}}. ¡Gracias por servir! — Parqueo La Guadalupe
   ```

   Parametros: {{1}} nombre, {{2}} fecha (ej. "domingo 29 de noviembre"), {{3}} hora de la misa (ej. "10:00 AM"), {{4}} hora de llegada, 30 minutos antes (ej. "9:30 AM").

### Variables de entorno

Agregarlas al servicio `parqueo-app` en el compose (y los valores secretos a `/root/.env`):

```yaml
      environment:
        - ADMIN_PASSWORD=${PARQUEO_ADMIN_PASSWORD}
        - WHATSAPP_TOKEN=${PARQUEO_WHATSAPP_TOKEN}
        - WHATSAPP_PHONE_NUMBER_ID=${PARQUEO_WHATSAPP_PHONE_NUMBER_ID}
        - WHATSAPP_TEMPLATE=recordatorio_parqueo
        - WHATSAPP_TEMPLATE_LANG=es
```

| Variable | Descripcion |
|---|---|
| `WHATSAPP_TOKEN` | Token permanente del usuario del sistema. |
| `WHATSAPP_PHONE_NUMBER_ID` | ID del numero en la Cloud API. |
| `WHATSAPP_TEMPLATE` | Nombre de la plantilla (por defecto `recordatorio_parqueo`). |
| `WHATSAPP_TEMPLATE_LANG` | Codigo de idioma de la plantilla (por defecto `es`). |
| `WHATSAPP_API_URL` | Opcional. Por defecto `https://graph.facebook.com/v21.0`. |

Si falta el token o el Phone Number ID, el sistema trabaja en **modo simulado**: no llama a WhatsApp y solo registra el estado `simulado`.

### Cron en el VPS

Comando (envia a quienes sirven **manana**; es idempotente y no repite mensajes ya enviados):

```bash
docker compose exec -T parqueo-app flask --app app enviar-recordatorios
```

Linea de cron del host (`crontab -e`):

```
0 14,15 * * * cd <ruta-del-compose> && docker compose exec -T parqueo-app flask --app app enviar-recordatorios --solo-hora 10 >> /var/log/parqueo-recordatorios.log 2>&1
```

Corre todos los dias a las 14:00 y 15:00 UTC, pero `--solo-hora 10` hace que solo actue cuando en Miami (America/New_York) sean las 10:00 AM, sin importar el horario de verano. Asi el sabado a las 10:00 AM se avisa a los del domingo, y cualquier otro dia se avisa a quienes tengan una misa especial al dia siguiente. Si no hay inscritos manana, no envia nada. Opciones: `--fecha YYYY-MM-DD` (otra fecha), `--simulado` (sin llamar a WhatsApp). Los errores se reintentan en la siguiente ejecucion.

### Como probar

- `docker compose exec -T parqueo-app flask --app app enviar-recordatorios --simulado --fecha 2026-11-29` registra el estado `simulado` sin enviar nada.
- En `/admin/recordatorios` (boton "Recordatorios" del panel) se ve el estado por servidor, se puede usar "Enviar recordatorios ahora" para la fecha elegida y "Enviar prueba" para mandar la plantilla con datos de ejemplo a un telefono (no se guarda en la base).

## Webhook de WhatsApp (estados de entrega)

Recibe de Meta los estados de cada mensaje (`sent`, `delivered`, `read`, `failed`) y los mensajes entrantes, y los guarda en la tabla `whatsapp_events`. Los `failed` quedan ademas en el log como `WARNING` con el codigo y titulo del error.

- URL de callback: `https://parqueo.guadalupedoral.app/webhooks/whatsapp`
- `WHATSAPP_VERIFY_TOKEN`: texto inventado por el administrador; se escribe igual en Meta al configurar el webhook.
- `WHATSAPP_APP_SECRET`: App secret en Meta (App settings > Basic > App secret); se usa para validar la firma `X-Hub-Signature-256`.

Lineas del compose (seccion `environment` de `parqueo-app`):

```yaml
- WHATSAPP_VERIFY_TOKEN=${PARQUEO_WHATSAPP_VERIFY_TOKEN}
- WHATSAPP_APP_SECRET=${PARQUEO_WHATSAPP_APP_SECRET}
```

En Meta, ir a WhatsApp > Configuration > Webhook, pegar la URL y el verify token, y suscribirse al campo `messages`.
