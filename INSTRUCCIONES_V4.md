# V4 — claridad de métricas y tablas

Reemplazar en GitHub:
- `app.py`
- `db.py`
- `assets/style.css`

No es necesario volver a ejecutar el agente solo por estos cambios visuales.

Cambios:
- `Baja contribución` pasa a `Productos con menor venta (pero sí vendieron)`; no usa margen.
- Las tablas de productos pasan a ancho completo para evitar columnas demasiado estrechas.
- `SKUs` se muestra como `SKU con venta` cuando el dato viene de ventas del período.
- En el diagnóstico se agrega `SKU asociados hoy`, `SKU con stock hoy` y una explicación explícita del período contra el que se calcula la variación.
- Cross-sell usa nombres más entendibles y explica confianza/afinidad (lift).
