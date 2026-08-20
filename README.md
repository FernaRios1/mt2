# Desempeño de Racks — Imperial (Dash)

Dashboard de decisión para pasillos/racks. La interfaz está diseñada para responder en este orden:

1. **Qué requiere atención**.
2. **Por qué lo recomienda**.
3. **Dónde está en el plano**.
4. **Qué productos/categorías explican el resultado**.
5. **Qué acción comercial o de surtido revisar**.

La app usa Python + Dash + Postgres y se despliega en Railway desde GitHub.

## Cambios de esta versión

- Centro de acciones YTD con prioridad **Alta / Media / Baja**.
- Recomendaciones explicables por rack:
  - **Proteger venta**: rack relevante que viene cayendo.
  - **Revisar rack**: baja venta + caída.
  - **Potenciar rack**: alta venta + crecimiento (o alta eficiencia cuando hay filtros).
  - **Optimizar surtido**: muchos SKU para una venta bajo la mediana.
  - **Mantener**: sin señal fuerte.
- El panel evita afirmar “rentabilidad” mientras el margen siga en $0. Se muestra **Modo desempeño**.
- KPIs del período con comparación contra período anterior.
- Mapa interactivo: clic en rack/pasillo abre diagnóstico y filtra el detalle.
- Navegación por pestañas: Racks, Productos, Categorías y Oportunidades.
- Productos con stock y sin venta priorizados con acción y motivo.
- Cross-sell queda como oportunidad comercial, separado de las decisiones de espacio.
- Año actual dinámico: ya no está fijo en 2026.
- El agente ahora refresca también `fact_pasillo_rack_anio` y `fact_producto_anio` para que las recomendaciones no queden congeladas en los seeds.

## Importante sobre el motor de recomendaciones

Por ahora **no existe una medida confiable de margen ni metros/m² de rack**. Por eso el motor no inventa una rentabilidad ni dice que un rack “debe” aumentar físicamente su espacio como hecho. En los racks fuertes propone **evaluar** más caras/exhibición, y en los débiles propone revisar surtido/espacio.

Cuando se incorpore margen y una medida de espacio, el paso natural es agregar `margen/m²`, `venta/m²` y un score económico real.

## Archivos a reemplazar en GitHub

- `app.py`
- `db.py`
- `agente_rentabilidad_rack.py`
- `assets/style.css`
- `README.md` (opcional, solo documentación)

No necesitas cambiar `Dockerfile`, `Procfile`, `schema.sql`, `auth.py` ni `requirements.txt` para este rediseño.

## Deploy

Railway está conectado al repo de GitHub. Después de subir/commitear los archivos anteriores a `main`, Railway debería lanzar un nuevo deployment automáticamente.

Comando de arranque esperado:

```bash
gunicorn app:server --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

Variables:

- `DATABASE_URL`: referencia al Postgres del proyecto.
- `APP_PASSWORD`: contraseña de acceso al dashboard.
- Para el agente local: `SQLSERVER_DSN` y `DATABASE_URL`.

## Sincronización

`agente_rentabilidad_rack.py` debe correr en un equipo Windows con acceso al SQL Server interno. Cada corrida actualiza el año actual, dimensiones, comparativos y cross-sell. La tabla de plano/coordenadas sigue administrándose desde la propia web.
