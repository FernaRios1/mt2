# Rentabilidad Rack — Imperial Ferretería (Dash)

Dashboard de venta/margen por pasillo y rack, con mapa de calor interactivo
(clic en un punto filtra toda la página), recomendación de espacio,
comparativo año a la fecha vs año anterior, y cross-sell/combos.
Python + Dash + Postgres, para desplegar en Railway.

## Qué hay en esta carpeta

```
app.py                          -- app Dash completa (dashboard + admin de planos)
auth.py                         -- contraseña simple
db.py                           -- todas las queries a Postgres + carga inicial automática
schema.sql                      -- estructura de la base
agente_rentabilidad_rack.py     -- se agenda con Task Scheduler para mantenerla al día
assets/style.css                -- tema oscuro/ámbar (Dash carga esta carpeta solo)
requirements.txt
seed_*.csv(.gz) / seed_plano_sanro.png   -- datos reales ya extraídos de tu pbix
```

## 1. Por qué Dash y no Streamlit

Streamlit reconstruye toda la página en cada interacción, lo que hace difícil
un patrón como "clic en el mapa filtra las demás tablas" sin que se sienta
lento o se pierda estado. Dash usa callbacks reales (como una app web
tradicional) — el clic en el mapa dispara solo las actualizaciones necesarias,
y el CSS es HTML/CSS de verdad (nada de los problemas de renderizado que
tuvimos con el CSS de Streamlit mostrándose como texto).

## 2. Desplegar en Railway

1. Sube esta carpeta a un repo de GitHub (o usa el CLI de Railway:
   `npm i -g @railway/cli`, `railway login`, `railway up` parado aquí).
2. En Railway: **New Project → Add a service → Database → PostgreSQL.**
3. **Add a service → GitHub Repo** (este repo) o despliega con el CLI.
4. En el servicio de la app, pestaña **Variables**:
   - `DATABASE_URL` → referencia al servicio de Postgres (botón "Reference variable").
   - `APP_PASSWORD` → la contraseña para entrar al panel.
   - `ALLOWED_IPS` → (opcional) IP fija de Imperial, si la consigues — ver más abajo.
5. **Settings → Deploy**, comando de arranque:
   ```
   gunicorn app:server --bind 0.0.0.0:$PORT --workers 2 --timeout 120
   ```
6. Railway entrega una URL pública — esa es la que comparten con el equipo.

## 3. La base se prepara sola

La primera vez que alguien abre la app con la base vacía, `db.ensure_ready()`
crea las tablas y carga los datos reales de `seed_*` (año 2026, ya con
familia/categoría/marca/stock/clasificación, comparativo 2025 vs 2026, y
5.000+ combinaciones de cross-sell). Tarda unos segundos la primera vez;
después no vuelve a tocar nada si detecta que ya hay datos.

## 4. Mantenerla al día

`agente_rentabilidad_rack.py` se agenda con el **Task Scheduler de Windows**
en un equipo con acceso a tu SQL Server (Railway no llega directo a tu red
interna). Cada corrida:
- trae venta/stock/marca/clasificación del año actual,
- recalcula pasillo/rack con la regla `Etiqueta_base` (validada, 99.2% exacta),
- recalcula el comparativo año a la fecha (venta, trx, clientes únicos vía `cod_entidad`),
- **recalcula todo el cross-sell** (soporte/confianza/lift) — medí esto contra
  el volumen real de boletas de 2026 (912 mil boletas, 13 tiendas) y tarda
  **~12 segundos**, así que correrlo a diario no debería ser un problema.

No lo pude probar contra tu SQL Server real — alguien con acceso tiene que
correrlo una vez a mano primero y avisarme si algún nombre de columna no
calza (`Marca`, `Stock_Disponible` y `cod_entidad` ya los confirmaste; el
resto sigue el mismo patrón de `mt2s.sql`).

## 5. Ir agregando el resto de las tiendas

Entra a **Administrar Planos** (link en el sidebar), elige la tienda, sube
la imagen del plano y un CSV con columnas `pasillo,x,y` o `rack,x,y` según
el nivel que quieras cargar. El mapa de calor de esa tienda queda funcionando
solo, sin tocar código.

## 6. Acceso restringido a la red de Imperial

Railway no tiene "solo esta IP" nativo en el plan gratis. Lo que dejé:
- **Contraseña obligatoria** (`APP_PASSWORD`) — el mínimo, siempre activo.
- **Allowlist de IP opcional** — si consigues la IP pública fija de la
  oficina, dime y te agrego la validación (queda listo el esqueleto en
  `auth.py`, solo falta engancharlo a un header `X-Forwarded-For` del proxy
  de Railway).

## 7. Bug de margen (heredado del origen)

El margen sigue en $0 porque el JOIN de costo en el SQL original tiene
`AND 1 = 0`. La venta es real; corrígelo en el origen cuando puedas y el
agente lo recoge solo la próxima vez que corra.
