# MT2 V8 — Venta neta sin IVA + NCV + historial permanente de ubicación

## Qué cambia

1. **FCV y BLV** se convierten a venta sin IVA:
   - `FCVdet.Total / 1.19`
   - `BLVdet.Total / 1.19`
2. **NCV** se resta usando la misma lógica del reporte entregado:
   - `NCVdet.Total / -1.19`
   - cantidad NCV con signo negativo.
3. El campo principal `venta` pasa a ser **venta neta sin IVA**.
4. Se guardan también por separado:
   - `venta_bruta`
   - `ncv`
   - `cantidad_bruta`
   - `cantidad_ncv`
5. **Cross-sell no usa NCV**. Se calcula solo con FCV + BLV positivas.
6. Se crea `hist_ubicacion_sku`, que conserva permanentemente los **cambios** de ubicación SKU → pasillo/rack. No guarda 1,2 millones de snapshots todos los días.
7. Una NCV se contabiliza en la **fecha de la NCV**, pero para asignarla físicamente a un rack se usa el SKU y la fecha del **documento original**. Así una devolución no se carga a un rack nuevo si el producto se movió después de la venta.
8. La cobertura física se calcula con valores absolutos para que las NCV negativas no distorsionen el porcentaje.

## Qué NO cambia todavía

- El stock sigue siendo el **snapshot actual**. V8 no crea aún histórico diario de stock.
- Margen sigue pendiente.
- Las recomendaciones de espacio siguen usando venta física neta y tendencia; `% NCV` se muestra como diagnóstico, pero todavía no se usa para disparar una acción automática porque primero conviene calibrar qué nivel de NCV es realmente alto para el negocio.

## Archivos a reemplazar

### En el PC que ejecuta el agente
Reemplazar:

`C:\Mts2\agente_rentabilidad_rack.py`

por el archivo V8.

### En GitHub / Railway
Reemplazar:

- `app.py`
- `db.py`
- `assets/style.css`
- `schema.sql` (recomendado para dejar el repositorio consistente; el agente y `db.py` también crean las columnas automáticamente).

## Orden recomendado

1. Reemplaza primero el agente local.
2. Conserva las variables de entorno `SQLSERVER_DSN` y `DATABASE_URL` que ya funcionan.
3. Ejecuta:

```bat
cd C:\Mts2
python agente_rentabilidad_rack.py
```

La consola debería mostrar algo parecido a:

```text
Ventas SQL: X líneas FCV/BLV + Y líneas NCV | bruta s/IVA $... | NCV $-... | neta $...
hist_ubicacion_sku: historia disponible desde ...
fact_venta_semana: ... filas
fact_venta_rack_dia: ... filas
Sincronización completa. OK V8 ... venta neta sin IVA ...
```

4. Si termina en `Sincronización completa`, sube los archivos web a GitHub y deja que Railway despliegue.
5. Haz `Ctrl + F5` en el dashboard.

## Validación importante de la primera corrida

Antes de usar las recomendaciones, compara una tienda y un período conocido con tu reporte actual. Debe cumplirse:

```text
Venta neta = Venta bruta sin IVA + NCV negativa
```

Ejemplo:

```text
Venta bruta s/IVA    $100.000.000
NCV                   -$5.000.000
Venta neta            $95.000.000
% NCV                        5,0%
```

Si el total no coincide con el reporte de control, no ajustar reglas del dashboard todavía: primero revisar el calce de NCV con `Tipo_DocumentoP + Nro_InternoP + Nro_LineaP`.

## Historial de ubicación

La primera ejecución solo puede recuperar la ventana que **todavía existe en INFSTOCK**. Desde ese momento, Postgres conserva los cambios y el historial comienza a crecer aunque INFSTOCK elimine meses antiguos.

Ejemplo guardado:

```text
SANRO | SKU 12345 | 2026-07-29 | 012 | 012-1-01
SANRO | SKU 12345 | 2026-08-13 | 012 | 012-1-03
SANRO | SKU 12345 | 2026-09-02 | 015 | 015-2-04
```

Si no cambia de rack, no se agrega una fila diaria.
