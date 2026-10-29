# Pomodoro Matías Henquín

Aplicación personal de estudio con interfaz adaptable, temporizador Pomodoro y una API FastAPI respaldada por PostgreSQL. La base se conectará a un proyecto Supabase exclusivo para Pomodoro; ninguna función de la aplicación depende de Google Sheets o Apps Script.

## Componentes

- `index.html`, `styles.css`, `app.js`: interfaz adaptable y gráficos calculados a partir de las sesiones obtenidas del servidor.
- `server.py`: API FastAPI, validación del JWT de Supabase, operaciones PostgreSQL, importación CSV y exportaciones CSV/XLSX.
- `migrations/001_pomodoro_sessions.sql`: tabla, índices, restricciones y RLS para la nueva base.
- `scripts/generate_seed_sql.py`: genera una migración privada idempotente desde el CSV local, conservando los IDs y las fechas.
- `manifest.json`, `sw.js`, `icon.svg`: instalación PWA y caché de recursos públicos únicamente.

## Desarrollo local

Python 3.11 o superior:

```powershell
python -m venv .venv
\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn server:app --reload
```

La portada abre en `http://127.0.0.1:8000`. Sin los valores de `.env`, el backend se mantiene cerrado y presenta que la configuración todavía no está disponible. No crea almacenamiento local alternativo.

## Configuración de Supabase

Crear el proyecto nuevo `pomodoro-matias` en la misma cuenta, sin seleccionar ni reutilizar `control-diego`. Aplicar la migración `001_pomodoro_sessions.sql` solo en el proyecto nuevo. Deshabilitar el alta pública de usuarios y el acceso anónimo. Crear o invitar únicamente al usuario autorizado; `signInWithOtp` usa `shouldCreateUser=false`. Configurar `SUPABASE_URL`, `SUPABASE_ANON_KEY` (clave publicable), `DATABASE_URL` (pooler PostgreSQL con SSL) y `POMODORO_ALLOWED_USER_ID`. FastAPI verifica el JWT de Supabase y limita toda lectura y escritura al UID autorizado; la clave service-role no se usa.

La clave anon/publishable que la aplicación entrega al navegador es pública por diseño; RLS la limita al propietario. La URL de PostgreSQL y el UUID de propietario son variables del backend y no se devuelven al navegador. FastAPI verifica el JWT de Supabase y acepta solamente el UUID configurado. Cada consulta SQL filtra además por ese UUID.

## Importación histórica

El CSV local se excluye de Git y no se modifica. `python scripts/generate_seed_sql.py` prepara `backups/private/002_seed_sessions.sql` y conserva los IDs originales. Antes de ejecutarlo en el proyecto nuevo, reemplazar el marcador por el UUID del único usuario Auth. La transacción es idempotente, calcula `total_minutos` en PostgreSQL y reporta altas, actualizaciones y conflictos de propietario. El endpoint autenticado `/api/import/csv` también acepta CSVs para futuras importaciones.

## Pruebas

```powershell
python -m unittest discover -s tests -v
```

Las pruebas cubren conversiones `HH:MM:SS`, encabezados/Unicode/fechas, validación de totales e IDs, protección de autenticación y ausencia de credenciales de base de datos en la configuración pública. Las pruebas con PostgreSQL real requieren el proyecto Supabase ya creado y configurado.

## Privacidad

`.env`, copias de seguridad, el CSV fuente y el SQL de carga histórica están excluidos de Git. No se guarda un historial local de sesiones ni se cachea información de la API en el service worker. El temporizador conserva localmente sus marcas de tiempo; si un ciclo se completa sin conexión, guarda una sola operación pendiente con ID estable para reintentarla sin duplicar registros.
