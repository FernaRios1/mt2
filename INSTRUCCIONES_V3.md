# Desempeño de Racks — V3

## Qué corrige esta versión

- Los filtros de familia, categoría, clasificación, zona, jefe de línea, marca y maneja stock se aplican al mapa, KPIs, acciones, productos, categorías y detalle.
- Los filtros avanzados son dependientes: al seleccionar una familia, las opciones de categoría se acotan a combinaciones existentes.
- La cola “Qué hacer primero” respeta Mes / Semana / Año y los filtros activos.
- El panel de diagnóstico muestra las categorías asociadas al rack o pasillo seleccionado, incluyendo surtido vigente sin venta después del primer sync V3.
- Hay botones visibles para descargar el detalle filtrado y los SKU con stock sin venta.
- “Stock sin venta” es deliberadamente YTD: significa stock positivo y ninguna venta en el año.
- Cross-sell es deliberadamente anual y se etiqueta como tal.

## Qué archivos reemplazar en GitHub

Reemplaza estos archivos del repo `mt2`:

- `app.py`
- `db.py`
- `schema.sql`
- `agente_rentabilidad_rack.py`
- `assets/style.css`

También puedes subir `requirements_agente.txt`, `1_PREPARAR_AGENTE.bat`, `2_ACTUALIZAR_DATA.bat` y `actualizar_data.sh`; no afectan Railway y sirven para mantener la data al día desde una máquina con acceso a SQL Server.

Después del commit, Railway debería desplegar automáticamente. Abre la web una vez para que `schema.sql` aplique las columnas nuevas de ubicación vigente.

## Cómo se actualiza la data

La web de Railway NO consulta directamente SQL Server de Imperial. La actualización la hace `agente_rentabilidad_rack.py` desde una máquina que sí tenga acceso a SQL Server y luego sube el resultado a Postgres de Railway.

### Primera vez en Windows

1. Copia/clona el repo en el PC o servidor que tenga acceso a SQL Server.
2. Ejecuta `1_PREPARAR_AGENTE.bat` una sola vez.
3. Configura como variables de entorno de Windows:
   - `SQLSERVER_DSN`
   - `DATABASE_URL`
4. Cierra y vuelve a abrir Terminal/PowerShell para que tome las variables.
5. Ejecuta `2_ACTUALIZAR_DATA.bat`.

No guardes ninguna de esas dos credenciales dentro de GitHub.

### Uso diario

Puedes hacer doble clic en `2_ACTUALIZAR_DATA.bat`. Si termina bien, el encabezado del dashboard mostrará la fecha/hora de la última sincronización.

Lo recomendable es programarlo con **Task Scheduler** una vez al día, después de que estén completas las cargas de ventas/stock del origen. El primer V3 puede tardar más porque reconstruye el año actual y el anterior al mismo corte.

## Qué analiza el agente V3

- Venta del año actual y del año anterior al mismo corte, hasta ayer.
- Stock y atributos vigentes de todos los SKU del snapshot de stock, incluso si no vendieron.
- Ubicación vigente Pasillo/Rack calculada con la regla de `Etiqueta_base`.
- Venta semanal por tienda/rack/SKU.
- Agregados anuales por rack y producto.
- Venta, transacciones y clientes para comparativo anual.
- Cross-sell del año actual usando un ID de transacción compuesto, no solo el número impreso.

Para evitar dobles conteos por cambios de ubicación, el agente reconstruye los hechos de los dos años que sincroniza en vez de dejar claves antiguas de rack.

### Importante sobre el comparativo histórico por rack

La fuente disponible entrega la **ubicación vigente** del SKU, no una foto histórica del rack de cada día. Por eso la venta del año anterior se atribuye a la ubicación actual del SKU. El comparativo responde bien a “¿cómo vendían el año pasado los productos que hoy componen este rack?”, pero no prueba que el producto estuviera físicamente en ese mismo rack el año pasado.

## Descargas de la web

- **Descargar detalle filtrado**: tienda + período + filtros + rack/pasillo seleccionado. Incluye SKU, descripción, marca, familia, subfamilia, categoría, clasificación, jefe de línea, zona, stock, venta y cantidad.
- **Descargar stock sin venta**: lista completa YTD de SKU con stock positivo y sin venta, con pasillo/rack vigente, categoría, venta año anterior, prioridad, acción y motivo.

Los CSV se generan con `;` para que sean cómodos de abrir en Excel con configuración regional de Chile.
