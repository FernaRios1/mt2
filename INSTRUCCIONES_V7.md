# MT2 V7 — claridad + filtros de ubicación + oportunidades

## Qué corrige

- Sidebar con contraste reforzado: período, Maneja stock y valores seleccionados se leen en blanco sobre fondo oscuro.
- Tooltip único: se eliminó el tooltip nativo del navegador que provocaba dos mensajes superpuestos.
- Nuevo filtro **Ubicación**:
  - Pasillo
  - Rack dependiente del pasillo
  Se aplica a diagnóstico, tablas, oportunidades, descargas y cross-sell.
- Si no existe un período físico anterior comparable, ya no muestra cuatro contadores en cero de Proteger/Revisar/Potenciar. Muestra únicamente señales que sí pueden calcularse (mix/cambio de rack) y avisa que la tendencia aún no es evaluable.
- Si la cobertura de ubicación física es menor a 90%, se muestra **Ubicación física parcial** y se pide validar antes de mover espacio.
- Mapa con puntos de venta mucho más contrastados; racks sin venta quedan pequeños y grises.
- **Stock sin venta**:
  - con `Maneja stock = No` ahora dice `No aplica`, no `0`;
  - si un rack seleccionado tiene 0 pero la tienda sí tiene oportunidades, se informa el total tienda;
  - la descarga mantiene el mismo alcance del filtro.
- Se oculta Jefe de línea porque `Responsable_Linea` no existe en el origen actual.
- Treemap pasa a **Familia → Categoría** cuando Jefe de línea no está disponible.
- Cross-sell agrega:
  - frecuencia normal del complemento;
  - compras A sin B / compras sin complemento;
  - lectura `Frecuente`, `Afinidad específica` o `Frecuente + específica`.
  Estas métricas sirven para priorizar pruebas de venta cruzada; no son una estimación de venta incremental.

## Archivos para GitHub

Reemplaza:

- `app.py`
- `db.py`
- `assets/style.css`

No hay cambio de esquema y no necesitas volver a cargar Postgres para que estas mejoras visuales/lógicas funcionen.

Se incluye también `agente_rentabilidad_rack.py` corregido (sin `Responsable_Linea`) para dejar la carpeta consistente con futuras sincronizaciones.

## Después de subir

1. Espera que Railway despliegue `mt2` y quede Online.
2. Recarga la web con Ctrl+F5.
3. Prueba:
   - Pasillo 012 → un rack del pasillo.
   - Maneja stock = No: stock sin venta debe decir `No aplica`.
   - Maneja stock = Sí: stock sin venta debe mostrar cantidad y detalle.
   - Ícono `i`: debe aparecer un solo mensaje.
