# MT2 V6 — Rack físico reciente + surtido vigente

## Qué cambia

La app deja de tratar una ubicación actual como si hubiera existido todo el año.

Ahora separa dos conceptos:

1. **Desempeño físico reciente**: solo para Mes/Semana completamente cubiertos por el historial de `SAV_CI_INFSTOCKBODEGA_DIARIO` (aprox. 3 meses). La venta se asigna al rack/pasillo donde el SKU estaba en la fecha de la venta.
2. **Surtido actual**: para períodos fuera de esa ventana (incluido Año). Las ventas se pueden analizar como desempeño de los productos que hoy componen el rack, pero la app lo rotula claramente y **no emite recomendaciones de espacio**.

Las variaciones de rack físico ya no usan 2025. Se comparan contra el período inmediatamente anterior dentro de la ventana física disponible:
- Agosto parcial -> mismos días de julio.
- Julio completo -> junio completo, si junio está cubierto.
- Semana -> mismos días de la semana anterior.

## Archivos que debes reemplazar en GitHub

- `app.py`
- `db.py`
- `agente_rentabilidad_rack.py`
- `schema.sql`
- `assets/style.css`

No necesitas cambiar requirements ni Postgres manualmente.

## Orden recomendado

1. Reemplaza los 5 archivos en GitHub.
2. Espera que Railway termine el deploy y quede `Online`.
3. La web puede mostrar inicialmente **"Aún no se ha cargado el historial físico reciente"**. Es normal.
4. En el PC/servidor que tiene acceso a SQL Server, ejecuta una vez el nuevo `agente_rentabilidad_rack.py` con las mismas variables `SQLSERVER_DSN` y `DATABASE_URL` que ya usabas.
5. Recarga la web.

## Qué hace la primera sincronización V6

- Sigue cargando las ventas del año actual y año anterior al mismo corte.
- Sigue actualizando stock y surtido vigente.
- Consulta hasta 100 días de INFSTOCK, pero **comprime en SQL solo los cambios de ubicación** para no transferir todos los snapshots diarios.
- Reconstruye la ubicación física de las ventas recientes.
- Crea/actualiza `fact_venta_rack_dia` en Postgres.
- Guarda la cobertura real por tienda en `sync_ubicacion_fisica`.

La primera corrida puede tardar más que la V3 porque SQL Server debe recorrer la ventana reciente de INFSTOCK.

## Cómo saber si está funcionando

En la web verás uno de estos modos:

### Rack físico verificado
Ejemplo:
`Historial disponible: 15/05/2026–19/08/2026 · cobertura de venta con ubicación: 97,8%`

En este modo:
- mapa = venta física del período;
- variación = contra período anterior físico;
- recomendaciones de espacio = activas;
- diagnóstico separa "qué vendió aquí" de "qué está asociado hoy".

### Vista de surtido actual
Se usa cuando el período es anterior al historial físico o cuando eliges Año.

En este modo:
- la app avisa que la venta histórica se atribuye a la ubicación vigente;
- sirve para analizar los productos que hoy componen el rack;
- **no se emiten recomendaciones de espacio**;
- no debe interpretarse como desempeño histórico del mismo mueble/rack físico.

## Caso importante: rack cambió

Si un rack tuvo venta física en el período pero hoy tiene `0 SKU asociados`, la app muestra una advertencia de cambio de planograma/codificación y evita presentar esa situación como una recomendación normal de espacio.

## Filtros

Los dropdowns del sidebar pasan a fondo oscuro y texto claro para evitar el problema de texto blanco sobre fondo blanco.

## Margen

Nada de esta V6 inventa rentabilidad. Hasta que el origen tenga margen válido, el dashboard sigue trabajando con venta, unidades, stock, surtido y afinidad de compra.
