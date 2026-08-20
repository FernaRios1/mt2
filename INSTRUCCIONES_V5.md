# MT2 V5 — claridad de indicadores y ayudas al pasar el mouse

## Qué cambia

1. **Sidebar legible**
   - Los dropdowns del panel izquierdo usan fondo blanco + texto oscuro.
   - Se corrigieron texto seleccionado, placeholder, flecha y opciones.

2. **Ayudas al pasar el mouse**
   - Los indicadores y secciones principales tienen un ícono `i`.
   - Al pasar el mouse se explica qué datos usa, cómo se calcula y qué NO significa.
   - Las tablas también muestran ayuda al pasar el mouse por encabezados clave.

3. **Enunciados más explícitos**
   - `Variación` → `Variación de venta`.
   - `Stock sin venta YTD` → `SKU con stock sin venta YTD`.
   - Cross-sell → `Productos que se compran juntos`.
   - Se aclara que cross-sell usa la tienda seleccionada y el año completo disponible; Mes/Semana no lo recalculan.

4. **Cross-sell más fácil de leer**
   - Se elimina `% boletas con ambos`, porque con el volumen total podía verse como 0,0% aunque la relación fuera real.
   - `Boletas juntas` → `Compras juntas`.
   - `% de A que también lleva B` → `% A → B`.
   - `Lift` se muestra como `Afinidad vs esperado`, por ejemplo `6,6×`.
   - 1× = lo esperable por frecuencia normal; 6,6× = aparecen juntas 6,6 veces más de lo esperable.

5. **Definiciones visibles en el dashboard**
   - Venta del período: suma de venta según tienda + período + filtros + sección seleccionada.
   - Variación de venta: compara contra el período comparable que se muestra bajo el KPI.
   - SKU con venta: SKU distintos que vendieron en ese período.
   - SKU asociados hoy: SKU del surtido/ubicación vigente de la sección.
   - SKU con stock hoy: SKU asociados con stock positivo en el snapshot actual.
   - SKU con stock sin venta YTD: stock positivo hoy y sin venta positiva durante el año actual.

## Cómo subir esta versión

Para esta mejora basta con reemplazar en GitHub:

- `app.py`
- `assets/style.css`

`db.py`, `schema.sql` y el agente no cambian respecto de V4.

Después de hacer commit, Railway debería desplegar automáticamente.
