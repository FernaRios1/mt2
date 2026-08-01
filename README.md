# Rentabilidad Rack — Imperial Ferretería

Dashboard de venta/margen por pasillo y rack, con mapa de calor sobre el
plano de cada tienda. Streamlit + Postgres, pensado para desplegar en
Railway, separado del Sistema de Planogramas existente.

## Qué hay en esta carpeta

```
app.py                          -- dashboard principal
pages/1_Administrar_Planos.py   -- subir plano + coordenadas por tienda
db.py                           -- todas las queries a Postgres
auth.py                         -- contraseña + allowlist de IP opcional
schema.sql                      -- estructura de la base
cargar_datos_iniciales.py       -- carga tus datos reales (2026) la primera vez
agente_rentabilidad_rack.py     -- se agenda con Task Scheduler para mantenerla al día
requirements.txt
seed_*.csv.gz / seed_plano_sanro.png   -- los datos reales que ya extraje de tu pbix
```

## 1. Crear el proyecto en Railway

1. Entra a [railway.app](https://railway.app) → **New Project**.
2. **Add a service → Database → PostgreSQL.** Railway la deja lista sola.
3. **Add a service → GitHub Repo** (sube esta carpeta a un repo tuyo primero,
   o usa **Deploy from local directory** con el CLI de Railway si no quieres
   pasar por GitHub: `npm i -g @railway/cli`, luego `railway login` y
   `railway up` parado en esta carpeta).
4. En el servicio de la app (no el de Postgres), pestaña **Variables**,
   agrega:
   - `DATABASE_URL` → Railway te la ofrece para copiar directo desde el
     servicio de Postgres (botón "Reference variable" o cópiala de la
     pestaña Connect de la base).
   - `APP_PASSWORD` → la contraseña que quieras para entrar al panel.
   - `ALLOWED_IPS` → (opcional) IP pública fija de Imperial, si la tienes.
5. En **Settings → Deploy**, comando de arranque:
   ```
   streamlit run app.py --server.port $PORT --server.address 0.0.0.0
   ```
6. Railway te da una URL pública (`algo.up.railway.app`) — esa es la que
   comparten con el equipo.

## 2. La base se prepara sola

No hace falta correr `cargar_datos_iniciales.py` a mano: la primera vez que
alguien abre la app (o la página Administrar Planos), si la base está vacía,
`db.ensure_ready()` crea las tablas y carga los datos reales empaquetados en
`seed_*.csv.gz` — tarda ~25-30 segundos esa primera vez (usa `COPY`, no
inserts fila por fila), y las siguientes veces no hace nada porque ya
detecta que la base tiene datos.

`cargar_datos_iniciales.py` sigue ahí por si prefieres correrlo tú a mano
antes de abrir la app, o para restaurar datos en otro ambiente.

## 3. Mantenerla al día

`agente_rentabilidad_rack.py` se agenda con el **Task Scheduler de Windows**
en un PC/servidor con acceso a tu SQL Server (Railway no puede llegar directo
a tu red interna). Hoy actualiza venta por producto — **todavía le falta
Pasillo/rack**, ver la nota al inicio de ese archivo. Avísame de dónde sale
esa asignación en tu Power Query y lo completo.

No lo pude probar contra tu SQL Server real — alguien con acceso tiene que
correrlo una vez a mano primero y avisarme si algo no calza (nombres de
base, de columnas, etc.).

## 4. Ir agregando el resto de las tiendas

Entra a la página **Administrar Planos** dentro de la app (menú de la
izquierda), elige la tienda, sube la imagen del plano y un CSV con columnas
`pasillo,x,y` (posición en píxeles sobre esa imagen). El mapa de calor de esa
tienda aparece solo, sin tocar código.

## Sobre el acceso restringido a la red de Imperial

Railway no tiene una opción nativa de "solo esta IP" en el plan gratis/hobby.
Lo que armé:
- **Contraseña obligatoria** (`APP_PASSWORD`) — funciona siempre, es lo mínimo.
- **Allowlist de IP opcional** (`ALLOWED_IPS`) — si Imperial tiene una IP
  pública fija para la oficina (pregunta a tu proveedor de internet o a
  soporte técnico), la agregas ahí y el panel queda cerrado a esa red aunque
  alguien tenga la contraseña. Si no la tienes, deja esa variable vacía por
  ahora y usa solo la contraseña.

## Novedades de esta versión

- **Filtros de producto** en el sidebar: Familia, Categoría, Clasificación SKU,
  Zona de picking, Jefe de línea, Maneja stock — igual que en el BI original,
  aplican sobre el mapa de calor, las tablas de pasillo/rack y las de producto
  a la vez (todo sale de una sola tabla `fact_venta_semana`).
- **Mapa de calor con selector Pasillo / Rack** — mismo plano, cambias el
  nivel y los puntos se recalculan solos. Rack necesita coordenadas más finas
  (`dim_rack_coord`, ya cargadas para SANRO desde "Puntos Planograma";
  para otras tiendas, súbelas en Administrar Planos igual que las de pasillo).
- **Tema visual** (`.streamlit/config.toml` + CSS en `app.py`) — sidebar oscuro,
  acento ámbar, tipografía monoespaciada en los números, para que se vea más
  parecido al HTML que armé antes que al Streamlit por defecto.

## Sobre compartir la versión HTML en vez de esto

Es posible, pero con un trade-off: el HTML standalone es una **foto fija**
(no se conecta a Postgres, no tiene filtros en vivo, no hay period picker
dinámico salvo lo que ya tenía embebido) — para compartirlo solo hay que
mandar el archivo, cualquiera lo abre con doble clic, no necesita nada
instalado. Esta app de Streamlit sí es "viva" (conectada a Postgres, con
filtros, con carga de nuevos planos) pero necesita estar desplegada en algún
lado (Railway) para que el equipo la vea por URL.

Si quieres lo mejor de los dos mundos, el camino natural es: dejar esta app
de Streamlit desplegada como la fuente de verdad, y generar snapshots HTML
descargables desde acá (un botón "Exportar HTML de esta vista") para cuando
alguien quiera mandar un reporte fijo por correo sin que el destinatario
necesite entrar al sistema. Puedo armar ese botón si te sirve.

## Sobre el bug de margen

El margen sale en $0 en todos los datos cargados porque el JOIN de costo en
el SQL original (`mt2s.sql`, bloque VENTAS) tiene `AND 1 = 0`, que lo anula
siempre. La venta es real; el margen se arregla solo una vez que corrijas esa
línea en el origen y vuelvas a correr el agente.
