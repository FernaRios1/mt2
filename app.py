import base64
import os

import dash
from dash import dcc, html, Input, Output, State, dash_table, callback_context
from dash.dash_table.Format import Format, Group, Scheme, Symbol
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import pandas as pd

import db
from auth import check_password


# =========================
# Formatos / utilidades
# =========================
def fmt_money(n):
    if n is None or pd.isna(n):
        return "—"
    return f"${float(n):,.0f}".replace(",", ".")


def fmt_money_short(n):
    if n is None or pd.isna(n):
        return "—"
    n = float(n)
    if abs(n) >= 1_000_000:
        return f"${n / 1_000_000:,.0f} MM".replace(",", ".")
    return fmt_money(n)


def fmt_pct(n, signed=True):
    if n is None or pd.isna(n):
        return "—"
    return f"{float(n):+.1f}%" if signed else f"{float(n):.1f}%"


def clean_records(df):
    if df is None or df.empty:
        return []
    return df.astype(object).where(pd.notna(df), None).to_dict("records")


MONTHS = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}

MONEY_FMT = Format(precision=0, scheme=Scheme.fixed, group=Group.yes,
                   symbol=Symbol.yes, symbol_prefix="$")
PCT_FMT = Format(precision=1, scheme=Scheme.fixed, group=Group.yes,
                 symbol=Symbol.yes, symbol_suffix="%")
NUM_FMT = Format(precision=0, scheme=Scheme.fixed, group=Group.yes)

FILTER_KEYS = ["familia", "categoria", "clasificacion", "zona_pck", "responsable_linea", "marca", "maneja_stock"]


# =========================
# App
# =========================
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    title="Desempeño de Racks — Imperial",
    suppress_callback_exceptions=True,
)
server = app.server

BASE_TABLE = dict(
    style_as_list_view=True,
    style_header={
        "backgroundColor": "#F8FAFC", "fontWeight": "700", "fontSize": "11px",
        "textTransform": "uppercase", "letterSpacing": ".04em", "color": "#64748B",
        "border": "none", "borderBottom": "1px solid #E2E8F0",
    },
    style_cell={
        "fontFamily": "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
        "fontSize": "13px", "padding": "10px 12px", "border": "none",
        "borderBottom": "1px solid #EEF2F7", "textAlign": "left",
        "whiteSpace": "normal", "height": "auto", "minWidth": "80px", "maxWidth": "360px",
    },
    style_data={"backgroundColor": "white", "color": "#0F172A"},
    page_size=10,
    sort_action="native",
    export_format="csv",
    tooltip_delay={"show": 350, "hide": 100},
    tooltip_duration=None,
    locale_format={"group": ".", "decimal": ","},
)

ACTION_STYLE = [
    {"if": {"filter_query": '{prioridad} = "Alta"', "column_id": "prioridad"},
     "backgroundColor": "#FEE2E2", "color": "#991B1B", "fontWeight": "700"},
    {"if": {"filter_query": '{prioridad} = "Media"', "column_id": "prioridad"},
     "backgroundColor": "#FEF3C7", "color": "#92400E", "fontWeight": "700"},
    {"if": {"filter_query": '{accion} = "Potenciar espacio"', "column_id": "accion"},
     "color": "#047857", "fontWeight": "700"},
    {"if": {"filter_query": '{accion} = "Proteger desempeño"', "column_id": "accion"},
     "color": "#B45309", "fontWeight": "700"},
    {"if": {"filter_query": '{accion} = "Revisar espacio"', "column_id": "accion"},
     "color": "#B91C1C", "fontWeight": "700"},
    {"if": {"state": "active"}, "backgroundColor": "#FFF7ED", "border": "1px solid #FDBA74"},
]


def info_tip(text):
    """Icono de ayuda: al posar el mouse (o foco) muestra cómo se calcula/interpreta."""
    return html.Span(
        "i", className="info-tip", tabIndex=0,
        **{"data-tip": text, "aria-label": text},
    )


def label_with_tip(label, tip=None, class_name="label-with-tip"):
    if not tip:
        return label
    return html.Span([html.Span(label), info_tip(tip)], className=class_name)


def section(title, children, subtitle=None, class_name="section-panel", tooltip=None):
    head = [html.Div([
        html.H2(label_with_tip(title, tooltip), className="section-title"),
        html.Div(subtitle, className="section-subtitle") if subtitle else None,
    ], className="section-head")]
    return html.Section([*head, *children], className=class_name)


def metric_card(label, value, helper=None, tone="default", tooltip=None):
    return html.Div([
        html.Div(label_with_tip(label, tooltip), className="metric-label"),
        html.Div(value, className="metric-value"),
        html.Div(helper, className=f"metric-helper metric-{tone}") if helper else None,
    ], className="metric-card")


def make_filters(maneja_sel, familia, categoria, clasificacion, zona_pck, responsable_linea, marca):
    return {
        "familia": familia or [],
        "categoria": categoria or [],
        "clasificacion": clasificacion or [],
        "zona_pck": zona_pck or [],
        "responsable_linea": responsable_linea or [],
        "marca": marca or [],
        "maneja_stock": ["S"] if maneja_sel == "Sí" else ["N"] if maneja_sel == "No" else [],
    }


def effective_section(seleccion=None, pasillo_ui=None, rack_ui=None):
    """Mapa/tabla manda si existe; si no, usa el filtro explícito de ubicación del sidebar."""
    if seleccion:
        if seleccion.get("nivel") == "rack" and seleccion.get("clave"):
            return None, str(seleccion["clave"])
        if seleccion.get("nivel") == "pasillo" and seleccion.get("clave"):
            return str(seleccion["clave"]), None
    if rack_ui:
        return None, str(rack_ui)
    if pasillo_ui:
        return str(pasillo_ui), None
    return None, None


# =========================
# Layout principal
# =========================
sidebar = html.Aside([
    html.Div([
        html.Div("IMPERIAL", className="brand-eyebrow"),
        html.Div("Desempeño de Racks", className="brand-mark"),
        html.Div("Venta, tendencia, surtido y oportunidades", className="brand-sub"),
    ], className="brand"),

    html.Div(label_with_tip("Tienda", "Todo el dashboard se calcula para la tienda seleccionada. Cross-sell también se limita a esta tienda."), className="side-label"),
    dcc.Dropdown(id="f-tienda", clearable=False, className="side-dd"),

    html.Div(label_with_tip("Período", "Mes/Semana dentro del historial de ubicación disponible usan el rack físico real de cada fecha. INFSTOCK conserva aprox. 3 meses. Para períodos más antiguos o Año, la vista usa el surtido/ubicación vigente y no emite recomendaciones de espacio."), className="side-label side-gap"),
    dcc.RadioItems(
        id="f-modo-periodo",
        options=[{"label": "Mes", "value": "Mes"}, {"label": "Semana", "value": "Semana"},
                 {"label": "Año", "value": "Año completo"}],
        value="Mes", className="period-segment", labelClassName="period-option",
        inputClassName="period-input",
    ),
    dcc.Dropdown(id="f-mes", clearable=False, className="side-dd"),
    dcc.Dropdown(id="f-semana", clearable=False, className="side-dd", style={"display": "none"}),


    html.Div(label_with_tip("Ubicación", "Filtro directo de navegación. Pasillo y rack se aplican a todo el detalle. Si haces clic en el mapa o en una acción, ese clic toma prioridad hasta que cambies este filtro."), className="side-label side-gap"),
    dcc.Dropdown(id="f-pasillo-ubic", clearable=True, placeholder="Todos los pasillos", className="side-dd"),
    dcc.Dropdown(id="f-rack-ubic", clearable=True, placeholder="Primero elige un pasillo", className="side-dd", disabled=True),
    html.Div("Puedes filtrar un pasillo completo o bajar a un rack específico.", className="side-location-help"),

    # Campo conservado en callbacks por compatibilidad; el origen real no dispone de Responsable_Linea.
    dcc.Dropdown(id="f-responsable_linea", multi=True, style={"display": "none"}),

    html.Details([
        html.Summary([html.Span("Filtros avanzados"), info_tip("Filtran venta, mapa, diagnóstico, productos y categorías. Las recomendaciones de espacio solo aparecen cuando el período tiene ubicación física histórica. Stock sin venta es YTD y cross-sell usa el año completo de la tienda.")], className="filters-summary"),
        html.Div([
            dcc.Dropdown(id="f-familia", multi=True, placeholder="Familia", className="side-dd"),
            dcc.Dropdown(id="f-categoria", multi=True, placeholder="Categoría", className="side-dd"),
            dcc.Dropdown(id="f-clasificacion", multi=True, placeholder="Clasificación SKU", className="side-dd"),
            dcc.Dropdown(id="f-zona_pck", multi=True, placeholder="Zona de picking", className="side-dd"),
            dcc.Dropdown(id="f-marca", multi=True, placeholder="Marca", className="side-dd"),
            html.Div("Maneja stock", className="side-mini-label"),
            dcc.RadioItems(id="f-maneja_stock", options=["Todos", "Sí", "No"], value="Todos",
                           className="side-radio", labelClassName="side-radio-item"),
        ], className="advanced-filters"),
    ], className="filters-details"),
    html.Div(id="active-filter-summary", className="active-filter-summary"),
    html.Button("Limpiar filtros", id="btn-clear-filters", n_clicks=0, className="ghost-button sidebar-clear"),

    html.Div(className="sidebar-spacer"),
    dcc.Link("Administrar planos", href="/admin", className="side-navlink"),
    html.Div("INFSTOCK conserva aprox. 3 meses de ubicación. Fuera de esa ventana la app analiza surtido actual, no desempeño físico histórico. Margen pendiente.", className="side-foot"),
], className="sidebar")


action_table = dash_table.DataTable(
    id="tabla-acciones-rack",
    **BASE_TABLE,
    style_data_conditional=ACTION_STYLE,
    cell_selectable=True,
    tooltip_header={
        "prioridad": "Orden de atención definido por el motor de recomendaciones. No usa margen.",
        "accion": "Acción sugerida solo con ubicación física histórica disponible. No usa margen ni comparación anual.",
        "venta": "Suma de venta del rack en el período y filtros activos.",
        "variacion_pct": "(Venta física actual - venta física del período inmediatamente anterior) / venta anterior. Solo dentro de la ventana histórica disponible.",
        "skus": "Cantidad de SKU distintos que tuvieron venta en ese rack durante el período seleccionado.",
        "venta_por_sku": "Venta del rack dividida por la cantidad de SKU distintos con venta. No es margen ni rentabilidad.",
        "motivo": "Señal concreta que hizo que el rack recibiera esa recomendación.",
    },
)

main = html.Main([
    html.Div(id="header-tienda"),
    html.Div(id="margen-status"),
    html.Div(id="kpi-row", className="metric-grid"),

    section("Qué hacer primero", [
        html.Div(id="action-summary"),
        html.Div("Haz clic en una fila para analizar ese rack. Pasa el mouse sobre los íconos i o encabezados para ver cómo se calcula cada indicador.", className="micro-help"),
        action_table,
    ], subtitle="Recomendaciones de espacio solo cuando la ubicación física del período está disponible. Compara con el período inmediatamente anterior; no usa margen.",
       tooltip="Solo se activa con ubicación física histórica. Compara el mismo rack contra el período inmediatamente anterior dentro de la ventana de INFSTOCK. Proteger desempeño = alta venta física + caída; Revisar espacio = baja venta física + caída; Potenciar = alta venta física + crecimiento; Revisar mix = muchos SKU vendidos con venta bajo la mediana. No usa margen ni año anterior."),

    dbc.Row([
        dbc.Col(section("Mapa de la tienda", [
            html.Div([
                dbc.RadioItems(id="f-nivel-mapa", options=["Pasillo", "Rack"], value="Rack",
                               inline=True, className="nivel-toggle"),
                html.Div(id="coord-status"),
            ], className="map-toolbar"),
            dcc.Graph(id="mapa-calor", config={"displayModeBar": False, "scrollZoom": False}),
        ], subtitle="Cada punto representa un rack o pasillo; tamaño y color representan venta del período.",
           tooltip="El mapa usa la venta del período seleccionado y los filtros avanzados. No usa margen. Al hacer clic, todo el detalle se restringe al rack/pasillo elegido."), md=8),
        dbc.Col([
            section("Diagnóstico", [
                html.Div(id="selection-panel"),
                html.Button("Quitar selección", id="btn-clear-selection", n_clicks=0, className="ghost-button"),
            ], subtitle="Explica la sección seleccionada con venta, comparación, surtido vigente y categorías.",
               tooltip="Venta, unidades y SKU con venta provienen del período seleccionado. SKU asociados hoy y SKU con stock hoy provienen del surtido/stock vigente. La recomendación no utiliza margen."),
            section("Tendencia semanal", [
                dcc.Graph(id="tendencia-semanal", config={"displayModeBar": False}),
            ], class_name="section-panel compact-panel",
               tooltip="Suma la venta por semana del año actual para la misma tienda, filtros y rack/pasillo seleccionado. Sirve para ver tendencia; no es una comparación de margen."),
        ], md=4),
    ], className="g-3"),

    section("Explorar el porqué", [
        html.Div([
            html.Div("La app distingue desempeño físico reciente de surtido vigente. Si el período queda fuera del historial de ubicación (~3 meses), la vista se marca como surtido actual y no recomienda cambios de espacio.", className="micro-help"),
            html.Div([
                html.Button("Descargar detalle filtrado", id="btn-download-detalle", n_clicks=0, className="download-button"),
                html.Button("Descargar stock sin venta", id="btn-download-sinventa", n_clicks=0, className="download-button"),
            ], className="download-actions"),
        ], className="detail-toolbar"),
        dcc.Tabs(id="tabs-detalle", value="racks", className="detail-tabs", children=[
            dcc.Tab(label="Racks y pasillos", value="racks", className="detail-tab", selected_className="detail-tab-selected", children=[
                dbc.Row([
                    dbc.Col([
                        html.Div("Racks", className="subblock-title"),
                        dash_table.DataTable(id="tabla-racks", **BASE_TABLE, tooltip_header={
                            "skus": "SKU distintos con venta en el período, no todos los SKU asociados al rack.",
                            "venta": "Suma de venta del rack con período y filtros activos.",
                            "venta_por_sku": "Venta del rack / SKU distintos con venta. No es margen.",
                            "unidades": "Suma de cantidad vendida del rack.",
                        }),
                    ], md=7),
                    dbc.Col([
                        html.Div("Pasillos", className="subblock-title"),
                        dash_table.DataTable(id="tabla-pasillos", **BASE_TABLE, tooltip_header={
                            "racks": "Racks distintos con venta dentro del pasillo y universo filtrado.",
                            "skus": "SKU distintos con venta en el período.",
                            "venta": "Suma de venta del pasillo.",
                            "venta_por_rack": "Venta del pasillo / racks con venta. No es margen.",
                        }),
                    ], md=5),
                ], className="g-3 tab-content"),
            ]),
            dcc.Tab(label="Productos", value="productos", className="detail-tab", selected_className="detail-tab-selected", children=[
                html.Div([
                    html.Div(label_with_tip("Productos con mayor venta", "Ordena de mayor a menor la suma de venta del SKU en el período, tienda, filtros y sección seleccionada. No usa margen."), className="subblock-title"),
                    html.Div("Ordenados por venta del período seleccionado. No utiliza margen.", className="micro-help"),
                    dash_table.DataTable(id="tabla-top", **BASE_TABLE, tooltip_header={
                        "venta": "Suma de venta del SKU en el período y filtros activos.",
                        "cantidad": "Cantidad de unidades vendidas en el período.",
                        "stock": "Stock disponible del snapshot vigente; no es stock histórico del período.",
                    }),
                    html.Hr(className="soft-hr"),
                    html.Div(label_with_tip("Productos con menor venta (pero sí vendieron)", "Incluye únicamente SKU con venta positiva y los ordena desde la menor venta. No significa bajo margen, baja rentabilidad ni pérdida."), className="subblock-title"),
                    html.Div("Muestra los SKU con venta positiva más baja del período. No significa bajo margen ni pérdida.", className="micro-help"),
                    dash_table.DataTable(id="tabla-baja", **BASE_TABLE, tooltip_header={
                        "venta": "Suma de venta positiva del SKU. La tabla está ordenada desde la menor venta.",
                        "cantidad": "Cantidad vendida en el período.",
                        "stock": "Stock disponible actual; no es stock histórico.",
                    }),
                ], className="tab-content product-tables-full"),
            ]),
            dcc.Tab(label="Categorías", value="categorias", className="detail-tab", selected_className="detail-tab-selected", children=[
                html.Div([
                    html.Div("Familia → categoría", className="subblock-title"),
                    dcc.Graph(id="treemap-familia", config={"displayModeBar": False}),
                ], className="tab-content"),
            ]),
            dcc.Tab(label="Oportunidades", value="oportunidades", className="detail-tab", selected_className="detail-tab-selected", children=[
                html.Div(className="tab-content", children=[
                    html.Div(id="oportunidades-resumen"),
                    html.Div(label_with_tip("SKU con stock y sin venta YTD", "SKU que hoy maneja stock, tiene stock positivo y no registra venta positiva en todo el año actual. Respeta tienda, filtros y rack/pasillo seleccionado, pero no el mes/semana."), className="subblock-title"),
                    dash_table.DataTable(id="tabla-sinventa", **BASE_TABLE,
                                         style_data_conditional=ACTION_STYLE, tooltip_header={
                                             "stock": "Stock disponible positivo en el snapshot actual.",
                                             "venta_anio_anterior": "Venta del SKU en el año anterior disponible; se usa como señal para priorizar revisión.",
                                             "accion": "Sugerencia según si vendía el año anterior y si su categoría tiene venta.",
                                             "motivo": "Explicación de la señal que originó la acción.",
                                         }),
                    html.Hr(className="soft-hr"),
                    html.Div(label_with_tip("Complementos y afinidad de compra", "Cross-sell se calcula con transacciones de la tienda seleccionada y el año completo disponible. Mes/Semana no recalculan estas relaciones. Sirve para sugerir cercanía, bundles o venta asistida; no demuestra causalidad."), className="subblock-title"),
                    html.Div("Ámbito: tienda seleccionada · año completo disponible. Si eliges un rack/pasillo, se muestran relaciones vinculadas al surtido vigente hoy en esa sección.",
                             className="section-subtitle"),
                    html.Div([
                        html.Strong("Cómo leerlo: "),
                        html.Span("Compras juntas = transacciones con ambos. % A→B = de quienes compraron A, qué porcentaje también llevó B. Frecuencia normal de B = cuánto aparece B sin condicionar por A. Afinidad compara ambos porcentajes. Ocasiones A sin B = compras de A donde B no apareció: es un universo para probar cross-sell, no una venta garantizada."),
                    ], className="metric-explainer"),
                    dbc.Row([
                        dbc.Col([
                            dcc.Dropdown(id="combo-orden",
                                         options=[{"label": "Más compras juntas", "value": "boletas"},
                                                  {"label": "Mayor afinidad vs esperado", "value": "lift"},
                                                  {"label": "Mayor % de A que también lleva B", "value": "confianza"}],
                                         value="boletas", clearable=False),
                            dash_table.DataTable(id="tabla-combos", **BASE_TABLE, tooltip_header={
                                "boletas": "Cantidad de transacciones de la tienda donde aparecen ambos productos.",
                                "confianza_pct": "De todas las transacciones que contienen Producto A, porcentaje que también contiene Producto B.",
                                "frecuencia_base_pct": "Frecuencia normal de B en las compras de la tienda. Ayuda a entender si un lift alto se debe a que B es muy poco frecuente.",
                                "afinidad_txt": "Lift: P(B|A) / P(B). 2× = se compran juntos 2 veces más de lo esperable; 1× = sin asociación especial; <1× = menos de lo esperable.",
                                "oportunidad_sin_b": "Transacciones con A donde B no apareció. Es un universo potencial para probar recomendación/complemento; no es una estimación de venta incremental.",
                                "senal": "Lectura simple de frecuencia y especificidad de la relación; no usa margen.",
                            }),
                        ], md=6),
                        dbc.Col([
                            dcc.Dropdown(id="combo-producto", placeholder="Buscar producto para ver qué se compra junto…"),
                            dash_table.DataTable(id="tabla-combos-producto", **BASE_TABLE, tooltip_header={
                                "boletas": "Cantidad de transacciones donde aparecen juntos el producto seleccionado y el relacionado.",
                                "confianza_pct": "De las compras que contienen el producto seleccionado, porcentaje que también llevan este producto relacionado.",
                                "frecuencia_base_pct": "Frecuencia normal del complemento en la tienda, sin condicionar por el producto seleccionado.",
                                "afinidad_txt": "Lift: compara esa probabilidad con la frecuencia normal del producto relacionado en la tienda. 1× = esperado; >1× = asociación positiva.",
                                "oportunidad_sin_complemento": "Compras del producto seleccionado donde este complemento no apareció. Úsalo como tamaño de universo para probar venta cruzada; no como pronóstico.",
                                "senal": "Frecuente = alto % de adopción; Específica = afinidad alta; ambas = complemento especialmente interesante.",
                            }),
                        ], md=6),
                    ], className="g-3"),
                ]),
            ]),
        ]),
    ], subtitle="El clic del mapa o de la cola de acciones filtra el detalle para explicar qué está pasando."),

    section("Contexto anual", [
        html.Div(id="comparativo-cards"),
    ], subtitle="Comparativo agregado de la tienda completa. No cambia con filtros de producto ni con el clic en un rack.",
       tooltip="Usa fact_comparativo_anio de la tienda: venta, transacciones, clientes únicos y ticket promedio por año disponible. Es contexto de tienda, no del rack seleccionado."),

    dcc.Download(id="download-detalle"),
    dcc.Download(id="download-sinventa"),
    dcc.Store(id="store-seleccion", data=None),
], className="main")

app.layout = html.Div([
    dcc.Location(id="url"),
    dcc.Store(id="store-auth", storage_type="session"),
    html.Div(id="page-content"),
])

login_layout = html.Div([
    html.Div([
        html.Div("IMPERIAL", className="brand-eyebrow"),
        html.H1("Desempeño de Racks", className="login-title"),
        html.P("Ingresa para continuar", className="login-subtitle"),
        dcc.Input(id="login-pwd", type="password", placeholder="Contraseña", className="login-input"),
        html.Button("Entrar", id="login-btn", className="primary-button"),
        html.Div(id="login-error", className="login-error"),
    ], className="login-box")
], className="login-wrap")

dashboard_layout = html.Div([sidebar, main], className="shell")


# =========================
# Admin de planos (se mantiene simple)
# =========================
admin_sidebar = html.Aside([
    html.Div([
        html.Div("IMPERIAL", className="brand-eyebrow"),
        html.Div("Administrar Planos", className="brand-mark"),
        html.Div("Imagen + coordenadas", className="brand-sub"),
    ], className="brand"),
    dcc.Link("Volver al dashboard", href="/", className="side-navlink"),
    html.Div("Tienda", className="side-label side-gap"),
    dcc.Dropdown(id="admin-tienda", clearable=False, className="side-dd"),
], className="sidebar")

admin_main = html.Main([
    html.Div([html.H1("Administrar planos", className="page-title")], className="page-header"),
    section("1. Imagen del plano", [
        dcc.Upload(id="admin-upload-imagen", children=html.Div(["Arrastra o ", html.A("elige un archivo")]),
                   className="upload-box", accept="image/png,image/jpeg"),
        html.Div(id="admin-imagen-preview"),
        html.Button("Guardar plano", id="admin-guardar-plano", className="primary-button small-button"),
        html.Div(id="admin-plano-msg"),
    ]),
    section("2. Coordenadas", [
        dbc.RadioItems(id="admin-nivel", options=["Pasillo", "Rack"], value="Pasillo", inline=True),
        html.Div("CSV con columnas pasillo|rack, x, y. La esquina superior izquierda es 0,0.", className="section-subtitle"),
        dcc.Upload(id="admin-upload-csv", children=html.Div(["Arrastra o ", html.A("elige un CSV")]),
                   className="upload-box", accept=".csv"),
        html.Button("Guardar coordenadas", id="admin-guardar-coords", className="primary-button small-button"),
        html.Div(id="admin-coords-msg"),
    ]),
    section("Coordenadas actuales", [
        dbc.Row([
            dbc.Col([html.Div("Por pasillo", className="subblock-title"),
                     dash_table.DataTable(id="admin-tabla-pasillo", **BASE_TABLE)], md=6),
            dbc.Col([html.Div("Por rack", className="subblock-title"),
                     dash_table.DataTable(id="admin-tabla-rack", **BASE_TABLE)], md=6),
        ], className="g-3"),
    ]),
    dcc.Store(id="admin-imagen-store"),
], className="main")

admin_layout = html.Div([admin_sidebar, admin_main], className="shell")


# =========================
# Routing / login
# =========================
@app.callback(Output("page-content", "children"), Input("store-auth", "data"), Input("url", "pathname"))
def _route(auth_ok, pathname):
    if not auth_ok:
        return login_layout
    if pathname == "/admin":
        return admin_layout
    return dashboard_layout


@app.callback(
    Output("store-auth", "data"), Output("login-error", "children"),
    Input("login-btn", "n_clicks"), State("login-pwd", "value"), prevent_initial_call=True,
)
def _login(n, pwd):
    if check_password(pwd):
        return True, ""
    return False, "Contraseña incorrecta."


# =========================
# Inicialización
# =========================
_INIT_DONE = {"ok": False}


def _lazy_init():
    if not _INIT_DONE["ok"]:
        db.ensure_ready()
        _INIT_DONE["ok"] = True


@app.callback(Output("f-tienda", "options"), Output("f-tienda", "value"), Input("store-auth", "data"))
def _init_tiendas(auth_ok):
    if not auth_ok:
        return [], None
    _lazy_init()
    tiendas = db.get_tiendas()
    opts = [{"label": f"{r.cod_tienda} — {r.nombre or r.tipo}", "value": r.cod_tienda} for r in tiendas.itertuples()]
    # SANRO primero si existe, porque es la tienda que ya tiene plano completo cargado.
    default = "SANRO" if "SANRO" in tiendas["cod_tienda"].tolist() else (tiendas["cod_tienda"].iloc[0] if len(tiendas) else None)
    return opts, default


@app.callback(
    Output("f-mes", "options"), Output("f-mes", "value"), Output("f-mes", "style"),
    Output("f-semana", "options"), Output("f-semana", "value"), Output("f-semana", "style"),
    Input("f-tienda", "value"), Input("f-modo-periodo", "value"),
)
def _periodo_opts(tienda, modo):
    if not tienda:
        return [], None, {"display": "none"}, [], None, {"display": "none"}
    meses, semanas = db.get_periodos(tienda)
    if meses.empty:
        return [], None, {"display": "none"}, [], None, {"display": "none"}
    anio = int(meses["anio"].max())
    meses_disp = meses[meses["anio"] == anio]["mes"].astype(int).tolist()
    semanas_disp = semanas[semanas["anio"] == anio]["semana"].astype(int).tolist()
    mes_opts = [{"label": MONTHS.get(m, f"Mes {m}"), "value": m} for m in meses_disp]
    sem_opts = [{"label": f"Semana {s}", "value": s} for s in semanas_disp]
    mostrar_mes = {"display": "block"} if modo == "Mes" else {"display": "none"}
    mostrar_sem = {"display": "block"} if modo == "Semana" else {"display": "none"}
    semana_default = (semanas_disp[-2] if len(semanas_disp) >= 2 else (semanas_disp[-1] if semanas_disp else None))
    return mes_opts, (meses_disp[-1] if meses_disp else None), mostrar_mes, \
        sem_opts, semana_default, mostrar_sem


@app.callback(Output("f-pasillo-ubic", "options"), Input("f-tienda", "value"))
def _ubic_pasillos(tienda):
    if not tienda:
        return []
    df = db.get_pasillos_disponibles(tienda)
    return [{"label": f"Pasillo {r.pasillo}", "value": str(r.pasillo)} for r in df.itertuples()]


@app.callback(Output("f-rack-ubic", "options"), Output("f-rack-ubic", "disabled"),
              Input("f-tienda", "value"), Input("f-pasillo-ubic", "value"))
def _ubic_racks(tienda, pasillo):
    if not tienda or not pasillo:
        return [], True
    df = db.get_racks_disponibles(tienda, pasillo=pasillo)
    return ([{"label": f"Rack {r.rack}", "value": str(r.rack)} for r in df.itertuples()], False)


@app.callback(
    Output("f-familia", "options"), Output("f-categoria", "options"), Output("f-clasificacion", "options"),
    Output("f-zona_pck", "options"), Output("f-responsable_linea", "options"), Output("f-marca", "options"),
    Input("f-tienda", "value"), Input("f-familia", "value"), Input("f-categoria", "value"),
    Input("f-clasificacion", "value"), Input("f-zona_pck", "value"),
    Input("f-responsable_linea", "value"), Input("f-marca", "value"), Input("f-maneja_stock", "value"),
)
def _filtro_opts(tienda, familia, categoria, clasificacion, zona_pck, responsable, marca, maneja):
    if not tienda:
        return [], [], [], [], [], []
    filtros = make_filters(maneja, familia, categoria, clasificacion, zona_pck, responsable, marca)
    op = db.get_opciones_filtro_dependientes(tienda, filtros=filtros)
    return (op["familia"], op["categoria"], op["clasificacion"], op["zona_pck"],
            op["responsable_linea"], op["marca"])


@app.callback(
    Output("f-familia", "value"), Output("f-categoria", "value"), Output("f-clasificacion", "value"),
    Output("f-zona_pck", "value"), Output("f-responsable_linea", "value"), Output("f-marca", "value"),
    Output("f-maneja_stock", "value"), Output("f-pasillo-ubic", "value"), Output("f-rack-ubic", "value"),
    Input("btn-clear-filters", "n_clicks"), Input("f-tienda", "value"), prevent_initial_call=True,
)
def _limpiar_filtros(_, _tienda):
    return [], [], [], [], [], [], "Todos", None, None


@app.callback(
    Output("active-filter-summary", "children"),
    Input("f-familia", "value"), Input("f-categoria", "value"), Input("f-clasificacion", "value"),
    Input("f-zona_pck", "value"), Input("f-responsable_linea", "value"), Input("f-marca", "value"),
    Input("f-maneja_stock", "value"), Input("f-pasillo-ubic", "value"), Input("f-rack-ubic", "value"),
)
def _resumen_filtros(familia, categoria, clasificacion, zona_pck, responsable, marca, maneja, pasillo_ubic, rack_ubic):
    items = []
    etiquetas = [("Familia", familia), ("Categoría", categoria), ("Clasificación", clasificacion),
                 ("Zona", zona_pck), ("Jefe", responsable), ("Marca", marca)]
    for label, vals in etiquetas:
        if vals:
            items.append(f"{label}: {', '.join(map(str, vals[:2]))}{'…' if len(vals) > 2 else ''}")
    if maneja and maneja != "Todos":
        items.append(f"Maneja stock: {maneja}")
    if rack_ubic:
        items.append(f"Rack: {rack_ubic}")
    elif pasillo_ubic:
        items.append(f"Pasillo: {pasillo_ubic}")
    if not items:
        return "Sin filtros avanzados"
    return html.Div([html.Span(x, className="filter-chip") for x in items], className="filter-chip-wrap")


# =========================
# Selección: mapa o cola de acciones
# =========================
@app.callback(
    Output("store-seleccion", "data"),
    Input("mapa-calor", "clickData"),
    Input("tabla-acciones-rack", "active_cell"),
    Input("btn-clear-selection", "n_clicks"),
    Input("f-tienda", "value"), Input("f-mes", "value"), Input("f-semana", "value"),
    Input("f-nivel-mapa", "value"), Input("f-familia", "value"), Input("f-categoria", "value"),
    Input("f-clasificacion", "value"), Input("f-zona_pck", "value"),
    Input("f-responsable_linea", "value"), Input("f-marca", "value"), Input("f-maneja_stock", "value"),
    Input("f-pasillo-ubic", "value"), Input("f-rack-ubic", "value"),
    State("tabla-acciones-rack", "data"), State("store-seleccion", "data"),
    prevent_initial_call=True,
)
def _seleccionar(click_mapa, action_cell, clear_clicks, tienda, mes, semana, nivel,
                  familia, categoria, clasificacion, zona_pck, responsable, marca, maneja,
                  pasillo_ubic, rack_ubic, action_data, actual):
    trig = callback_context.triggered_id
    reset_ids = {"f-tienda", "f-mes", "f-semana", "f-nivel-mapa", "f-familia", "f-categoria",
                 "f-clasificacion", "f-zona_pck", "f-responsable_linea", "f-marca", "f-maneja_stock",
                 "f-pasillo-ubic", "f-rack-ubic"}
    if trig in reset_ids or trig == "btn-clear-selection":
        return None
    if trig == "tabla-acciones-rack" and action_cell and action_data:
        rack = action_cell.get("row_id")
        if rack:
            return {"nivel": "rack", "clave": rack}
        idx = action_cell.get("row")
        if idx is not None and 0 <= idx < len(action_data):
            rack = action_data[idx].get("rack")
            if rack:
                return {"nivel": "rack", "clave": rack}
    if trig == "mapa-calor" and click_mapa:
        clave = click_mapa["points"][0].get("customdata")
        if clave:
            nivel_key = "pasillo" if nivel == "Pasillo" else "rack"
            if actual and actual.get("nivel") == nivel_key and actual.get("clave") == clave:
                return None
            return {"nivel": nivel_key, "clave": clave}
    return actual


# =========================
# Centro de acciones del período
# =========================
@app.callback(
    Output("action-summary", "children"),
    Output("tabla-acciones-rack", "data"), Output("tabla-acciones-rack", "columns"),
    Input("f-tienda", "value"), Input("f-modo-periodo", "value"), Input("f-mes", "value"), Input("f-semana", "value"),
    Input("f-familia", "value"), Input("f-categoria", "value"), Input("f-clasificacion", "value"),
    Input("f-zona_pck", "value"), Input("f-responsable_linea", "value"), Input("f-marca", "value"), Input("f-maneja_stock", "value"),
    Input("f-pasillo-ubic", "value"), Input("f-rack-ubic", "value"),
)
def _acciones_rack(tienda, modo, mes, semana, familia, categoria, clasificacion, zona_pck, responsable, marca, maneja, pasillo_ubic, rack_ubic):
    if not tienda:
        return None, [], []
    anio = db.get_anio_actual(tienda)
    filtros = make_filters(maneja, familia, categoria, clasificacion, zona_pck, responsable, marca)
    mes_sel = mes if modo == "Mes" else None
    semana_sel = semana if modo == "Semana" else None
    ctx = db.get_contexto_ubicacion_fisica(tienda, anio, mes_sel, semana_sel)

    if not ctx.get("cubierto"):
        rango = (f"{ctx['desde']:%d/%m/%Y}–{ctx['hasta']:%d/%m/%Y}" if ctx.get("desde") else "aún no cargado")
        summary = html.Div([
            html.Div([
                html.Span("Sin recomendación de espacio", className="mode-pill"),
                html.Strong("Este período no tiene ubicación física histórica completa."),
            ], className="action-hero-top"),
            html.P(ctx.get("motivo"), className="action-hero-text"),
            html.P(f"Historial físico disponible: {rango}. Puedes seguir analizando venta y surtido actual, pero no sería correcto afirmar que el mismo rack físico subió o bajó.", className="action-hero-reco"),
        ], className="action-summary")
        return summary, [], []

    df = db.get_acciones_rack_fisico(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros)
    if df.empty:
        return html.Div("No hay datos físicos suficientes para construir recomendaciones."), [], []

    # El motor se calcula contra todos los racks del universo filtrado; la ubicación del sidebar
    # solo acota qué señales se muestran, sin alterar los percentiles de referencia.
    df_scope = df.copy()
    if rack_ubic:
        df_scope = df_scope[df_scope["rack"].astype(str) == str(rack_ubic)]
    elif pasillo_ubic:
        df_scope = df_scope[df_scope["pasillo"].astype(str) == str(pasillo_ubic)]
    if df_scope.empty:
        return html.Div("No hay racks con venta física para la ubicación seleccionada y los filtros actuales.", className="empty-note"), [], []

    df = df_scope
    accionables = df[df["accion"] != "Mantener"].copy()
    alta = int((df["prioridad"] == "Alta").sum())
    proteger = int((df["accion"] == "Proteger desempeño").sum())
    revisar = int((df["accion"] == "Revisar espacio").sum())
    potenciar = int((df["accion"] == "Potenciar espacio").sum())
    mix = int((df["accion"] == "Revisar mix").sum())
    cambios = int((df["accion"] == "Rack cambió").sum())

    top = accionables.iloc[0] if len(accionables) else df.iloc[0]
    comp_label = df.iloc[0].get("comparacion_label") if len(df) else None
    periodo_actual = (f"Semana {semana_sel}" if semana_sel else
                      MONTHS.get(int(mes_sel), f"Mes {mes_sel}") if mes_sel else f"Año {anio}")
    cov = ctx.get("cobertura_venta_pct")
    cov_note = (f" Cobertura de venta con ubicación: {cov:.1%}." if cov is not None else "")
    if cov is not None and cov < 0.90:
        cov_note += " Cobertura bajo 90%: validar los racks críticos antes de ejecutar cambios de espacio."
    comparacion_note = (f"Desempeño físico de {periodo_actual} vs {comp_label}. "
                        "No usa año anterior: INFSTOCK no conserva ubicación histórica suficiente para eso." + cov_note
                        if comp_label else
                        f"Desempeño físico de {periodo_actual}; no hay período anterior completamente cubierto dentro del historial disponible." + cov_note)

    if comp_label:
        stats = html.Div([
            html.Div([html.Strong(alta), html.Span("prioridad alta")], className="mini-stat"),
            html.Div([html.Strong(proteger), html.Span("proteger desempeño")], className="mini-stat"),
            html.Div([html.Strong(revisar), html.Span("revisar espacio")], className="mini-stat"),
            html.Div([html.Strong(potenciar), html.Span("potenciar")], className="mini-stat"),
            html.Div([html.Strong(mix), html.Span("revisar mix")], className="mini-stat"),
            html.Div([html.Strong(cambios), html.Span("racks cambiados")], className="mini-stat"),
        ], className="mini-stat-grid")
    else:
        stats = html.Div([
            html.Div([html.Strong(mix), html.Span("revisar mix")], className="mini-stat"),
            html.Div([html.Strong(cambios), html.Span("racks cambiados")], className="mini-stat"),
            html.Div([html.Strong("—"), html.Span("tendencia aún no evaluable")], className="mini-stat mini-stat-disabled"),
        ], className="mini-stat-grid mini-stat-grid-3")

    summary = html.Div([
        html.Div([
            html.Div([html.Span(top["prioridad"], className=f"priority-pill priority-{str(top['prioridad']).lower()}"),
                      html.Span(f"Rack {top['rack']} · Pasillo {top['pasillo']}", className="action-location")], className="action-hero-top"),
            html.H3(top["accion"], className="action-hero-title"),
            html.P(top["motivo"], className="action-hero-text"),
            html.P(top["recomendacion"], className="action-hero-reco"),
        ], className="action-hero"),
        stats,
        html.Div(comparacion_note, className="engine-note"),
    ], className="action-summary")

    view = accionables[["prioridad", "accion", "pasillo", "rack", "venta", "variacion_pct",
                        "skus", "skus_asociados_hoy", "venta_por_sku", "motivo"]].head(250).copy()
    view["id"] = view["rack"].astype(str)
    cols = [
        {"name": "Prioridad", "id": "prioridad"}, {"name": "Acción", "id": "accion"},
        {"name": "Pasillo", "id": "pasillo"}, {"name": "Rack", "id": "rack"},
        {"name": "Venta física", "id": "venta", "type": "numeric", "format": MONEY_FMT},
        {"name": "Variación vs período anterior", "id": "variacion_pct", "type": "numeric", "format": PCT_FMT},
        {"name": "SKU vendidos aquí", "id": "skus", "type": "numeric", "format": NUM_FMT},
        {"name": "SKU asociados hoy", "id": "skus_asociados_hoy", "type": "numeric", "format": NUM_FMT},
        {"name": "Venta / SKU vendido", "id": "venta_por_sku", "type": "numeric", "format": MONEY_FMT},
        {"name": "Por qué", "id": "motivo"},
    ]
    return summary, clean_records(view), cols


# =========================
# Dashboard principal
# =========================
@app.callback(
    Output("header-tienda", "children"), Output("margen-status", "children"), Output("kpi-row", "children"),
    Output("mapa-calor", "figure"), Output("coord-status", "children"), Output("tendencia-semanal", "figure"),
    Output("tabla-pasillos", "data"), Output("tabla-pasillos", "columns"),
    Output("tabla-racks", "data"), Output("tabla-racks", "columns"),
    Output("tabla-top", "data"), Output("tabla-top", "columns"),
    Output("tabla-baja", "data"), Output("tabla-baja", "columns"),
    Output("treemap-familia", "figure"),
    Output("oportunidades-resumen", "children"), Output("tabla-sinventa", "data"), Output("tabla-sinventa", "columns"),
    Output("comparativo-cards", "children"),
    Input("f-tienda", "value"), Input("f-modo-periodo", "value"), Input("f-mes", "value"), Input("f-semana", "value"),
    Input("f-familia", "value"), Input("f-categoria", "value"), Input("f-clasificacion", "value"),
    Input("f-zona_pck", "value"), Input("f-responsable_linea", "value"), Input("f-marca", "value"),
    Input("f-maneja_stock", "value"), Input("f-nivel-mapa", "value"), Input("store-seleccion", "data"),
    Input("f-pasillo-ubic", "value"), Input("f-rack-ubic", "value"),
)
def _actualizar(tienda, modo, mes, semana, familia, categoria, clasificacion, zona_pck,
                 responsable_linea, marca, maneja_sel, nivel_mapa_lbl, seleccion, pasillo_ubic, rack_ubic):
    if not tienda:
        return [dash.no_update] * 19

    anio = db.get_anio_actual(tienda)
    if anio is None:
        return [dash.no_update] * 19
    mes_sel = mes if modo == "Mes" else None
    semana_sel = semana if modo == "Semana" else None
    filtros = make_filters(maneja_sel, familia, categoria, clasificacion, zona_pck, responsable_linea, marca)
    nivel_mapa = "pasillo" if nivel_mapa_lbl == "Pasillo" else "rack"
    ctx = db.get_contexto_ubicacion_fisica(tienda, anio, mes_sel, semana_sel)
    fisico = bool(ctx.get("cubierto"))

    pasillo_f, rack_f = effective_section(seleccion, pasillo_ubic, rack_ubic)
    hay_seccion = bool(pasillo_f or rack_f)

    # Total tienda/producto es seguro sin ubicación. Al seleccionar una sección y existir
    # cobertura física, usamos la ubicación real que tenía cada SKU en la fecha de venta.
    if fisico and hay_seccion:
        resumen = db.get_resumen_fisico(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                        pasillo=pasillo_f, rack=rack_f) or {}
    else:
        resumen = db.get_resumen_periodo(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                         pasillo=pasillo_f, rack=rack_f)

    if fisico:
        pasillos = db.get_pasillo_resumen_fisico(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                                 pasillo=pasillo_f, rack=rack_f)
        racks = db.get_rack_detalle_fisico(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                           pasillo=pasillo_f, rack=rack_f)
    else:
        pasillos = db.get_pasillo_resumen(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                          pasillo=pasillo_f, rack=rack_f)
        racks = db.get_rack_detalle(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                    pasillo=pasillo_f, rack=rack_f)

    if fisico and hay_seccion:
        top = db.get_top_productos_fisico(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                          pasillo=pasillo_f, rack=rack_f, n=50)
        baja = db.get_top_productos_fisico(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                           pasillo=pasillo_f, rack=rack_f, n=50, ascendente=True)
        tree = db.get_treemap_fisico(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                     pasillo=pasillo_f, rack=rack_f)
    else:
        top = db.get_top_productos(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                   pasillo=pasillo_f, rack=rack_f, n=50)
        baja = db.get_top_productos(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                    pasillo=pasillo_f, rack=rack_f, n=50, ascendente=True)
        tree = db.get_treemap(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                              pasillo=pasillo_f, rack=rack_f)

    # Stock sin venta solo aplica a SKU que manejan stock. Si el usuario filtra Maneja stock=No,
    # mostramos N/A en vez de un 0 engañoso.
    if maneja_sel == "No":
        sinventa_count = None
        sinventa_total_tienda = None
        acciones_prod = pd.DataFrame()
    else:
        sinventa_count = db.get_sin_venta_count(tienda, anio, filtros=filtros, pasillo=pasillo_f, rack=rack_f)
        sinventa_total_tienda = db.get_sin_venta_count(tienda, anio, filtros=filtros)
        acciones_prod = db.get_acciones_producto(tienda, anio, filtros=filtros, n=250, pasillo=pasillo_f, rack=rack_f)

    tiendas = db.get_tiendas()
    row_store = tiendas[tiendas["cod_tienda"] == tienda]
    nombre = row_store.iloc[0]["nombre"] if len(row_store) and pd.notna(row_store.iloc[0]["nombre"]) else tienda
    tipo = row_store.iloc[0]["tipo"] if len(row_store) else ""
    if semana_sel:
        periodo_txt = f"Semana {semana_sel} · {anio}"
    elif mes_sel:
        periodo_txt = f"{MONTHS.get(int(mes_sel), f'Mes {mes_sel}')} · {anio}"
    else:
        periodo_txt = f"Año {anio}"

    selection_txt = f"Rack {rack_f}" if rack_f else f"Pasillo {pasillo_f}" if pasillo_f else None
    sync = db.get_sync_status()
    sync_txt = "Datos cargados desde Postgres"
    if sync and sync.get("ejecutado_en") is not None:
        ts = pd.to_datetime(sync["ejecutado_en"], errors="coerce")
        if pd.notna(ts):
            sync_txt = f"Última sincronización: {ts.strftime('%d/%m/%Y %H:%M')}"

    n_filtros = sum(len(v) for v in filtros.values())
    mode_chip = "Ubicación física" if fisico else "Surtido actual"
    header = html.Div([
        html.Div([
            html.Div("ANÁLISIS DE ESPACIO", className="page-eyebrow"),
            html.H1(f"{tienda} · {nombre}", className="page-title"),
            html.Div([
                html.Span(tipo, className="context-chip") if tipo else None,
                html.Span(periodo_txt, className="context-chip"),
                html.Span(mode_chip, className="context-chip context-selected"),
                html.Span(f"{n_filtros} filtros activos", className="context-chip") if n_filtros else None,
                html.Span(selection_txt, className="context-chip context-selected") if selection_txt else None,
            ], className="context-row"),
        ]),
        html.Div(sync_txt, className="sync-text"),
    ], className="page-header")

    banners = []
    if fisico:
        cov = ctx.get("cobertura_venta_pct")
        cov_txt = f" · cobertura de venta con ubicación: {cov:.1%}" if cov is not None else ""
        cobertura_parcial = cov is not None and cov < 0.90
        pill_txt = "Ubicación física parcial" if cobertura_parcial else "Rack físico verificado"
        banner_cls = "mode-banner mode-banner-warning" if cobertura_parcial else "mode-banner mode-banner-good"
        pill_cls = "mode-pill mode-pill-warning" if cobertura_parcial else "mode-pill mode-pill-good"
        extra = (" Cobertura bajo 90%: interpreta las recomendaciones como señales y valida los racks críticos antes de mover espacio."
                 if cobertura_parcial else "")
        banners.append(html.Div([
            html.Span(pill_txt, className=pill_cls),
            html.Span(f"Ubicación reconstruida según la fecha de cada venta. Historial disponible: {ctx['desde']:%d/%m/%Y}–{ctx['hasta']:%d/%m/%Y}{cov_txt}. Las variaciones de rack usan el período inmediatamente anterior dentro de esta ventana; no 2025.{extra}"),
        ], className=banner_cls))
    else:
        rango = f"{ctx['desde']:%d/%m/%Y}–{ctx['hasta']:%d/%m/%Y}" if ctx.get("desde") else "aún no cargado"
        banners.append(html.Div([
            html.Span("Vista de surtido actual", className="mode-pill"),
            html.Span(f"{ctx.get('motivo')} Para este período las ventas por rack se atribuyen a la ubicación vigente hoy. Sirve para analizar los productos del surtido actual, no para afirmar cómo rindió ese espacio físico. Historial físico: {rango}."),
        ], className="mode-banner"))
    if float(resumen.get("margen") or 0) == 0:
        banners.append(html.Div([
            html.Span("Margen pendiente", className="mode-pill"),
            html.Span("No se calcula rentabilidad. Los indicadores actuales son de venta, unidades, surtido, stock y afinidad de compra."),
        ], className="mode-banner mode-banner-neutral"))
    margen_status = html.Div(banners, className="mode-banner-stack")

    var = resumen.get("variacion_pct")
    var_tone = "good" if var is not None and var >= 0 else "bad" if var is not None else "muted"
    if fisico and hay_seccion:
        var_label = "Variación física"
        var_tip = "Compara la venta del mismo rack/pasillo físico contra el período inmediatamente anterior cubierto por INFSTOCK. No compara con el año anterior porque no existe ubicación histórica suficiente."
    else:
        var_label = "Variación del surtido"
        var_tip = "Fuera de la ventana física, la comparación atribuye ventas históricas a la ubicación que los SKU tienen hoy. Úsala para analizar el surtido vigente, no el mismo espacio físico."
    prev_helper = (f"vs {resumen.get('periodo_anterior')}: {fmt_money_short(resumen.get('venta_anterior'))}"
                   if resumen.get("periodo_anterior") else "Sin período comparable válido")
    racks_count = int(racks["rack"].nunique()) if len(racks) else 0
    skus_count = int(resumen.get("skus") or 0)
    kpis = [
        metric_card("Venta del período", fmt_money_short(resumen.get("venta")),
                    "Respeta tienda, período, filtros y selección",
                    tooltip="Suma de venta del universo visible. Si hay una sección seleccionada y el período está cubierto, usa su ubicación física real de la fecha."),
        metric_card(var_label, fmt_pct(var), prev_helper, var_tone, tooltip=var_tip),
        metric_card("Racks con venta", f"{racks_count:,}".replace(",", "."),
                    f"{skus_count:,} SKU con venta".replace(",", "."),
                    tooltip="Con ubicación física disponible cuenta racks donde realmente ocurrió venta en el período. Fuera de esa ventana cuenta racks atribuidos al surtido vigente."),
        metric_card(
            "SKU con stock sin venta YTD",
            "No aplica" if sinventa_count is None else f"{sinventa_count:,}".replace(",", "."),
            ("Maneja stock = No: este indicador requiere stock positivo" if sinventa_count is None
             else (f"{sinventa_count} en la selección · {sinventa_total_tienda} en la tienda" if hay_seccion
                   else "Stock > 0 y sin venta positiva en el año")),
            "muted" if sinventa_count is None else "bad" if sinventa_count else "good",
            tooltip="Solo evalúa SKU que manejan stock (S), tienen stock positivo hoy y no registran venta positiva en el año. Si filtras Maneja stock = No, el indicador no aplica."),
    ]

    fig_map = _figura_mapa(tienda, anio, mes_sel, semana_sel, filtros, nivel_mapa, seleccion, fisico=fisico)
    sin_coord = (db.get_sin_coordenadas_fisico(tienda, anio, mes=mes_sel, semana=semana_sel, nivel=nivel_mapa)
                 if fisico else db.get_sin_coordenadas(tienda, nivel=nivel_mapa))
    if len(sin_coord):
        coord_status = html.Span(f"{len(sin_coord)} con venta sin coordenada", className="coord-warning")
    else:
        coord_status = html.Span("Cobertura de coordenadas completa", className="coord-ok")

    if fisico and hay_seccion:
        tendencia = db.get_tendencia_semana_fisico(tienda, filtros=filtros, pasillo=pasillo_f, rack=rack_f)
    else:
        tendencia = db.get_tendencia_semana(tienda, anio, filtros=filtros, pasillo=pasillo_f, rack=rack_f)
    fig_trend = _figura_tendencia(tendencia, semana_sel)

    if len(pasillos):
        pasillos = pasillos.copy(); pasillos["venta_por_rack"] = pasillos["venta"] / pasillos["racks"].replace(0, pd.NA)
    if len(racks):
        racks = racks.copy(); racks["venta_por_sku"] = racks["venta"] / racks["skus"].replace(0, pd.NA)
    venta_col = "Venta física" if fisico else "Venta atribuida"
    cols_pasillo = [
        {"name": "Pasillo", "id": "pasillo"}, {"name": "Racks", "id": "racks", "type": "numeric", "format": NUM_FMT},
        {"name": "SKU con venta", "id": "skus", "type": "numeric", "format": NUM_FMT},
        {"name": venta_col, "id": "venta", "type": "numeric", "format": MONEY_FMT},
        {"name": "Venta / rack", "id": "venta_por_rack", "type": "numeric", "format": MONEY_FMT},
    ]
    cols_rack = [
        {"name": "Pasillo", "id": "pasillo"}, {"name": "Rack", "id": "rack"},
        {"name": "SKU con venta", "id": "skus", "type": "numeric", "format": NUM_FMT},
        {"name": venta_col, "id": "venta", "type": "numeric", "format": MONEY_FMT},
        {"name": "Venta / SKU", "id": "venta_por_sku", "type": "numeric", "format": MONEY_FMT},
        {"name": "Unidades", "id": "unidades", "type": "numeric", "format": NUM_FMT},
    ]
    prod_cols = [
        {"name": "SKU", "id": "cod_rapido"}, {"name": "Producto", "id": "descripcion"},
        {"name": "Categoría", "id": "categoria"}, {"name": "Familia", "id": "familia"},
        {"name": "Marca", "id": "marca"}, {"name": "Stock hoy", "id": "stock", "type": "numeric", "format": NUM_FMT},
        {"name": "Venta", "id": "venta", "type": "numeric", "format": MONEY_FMT},
        {"name": "Cantidad", "id": "cantidad", "type": "numeric", "format": NUM_FMT},
    ]

    if maneja_sel == "No":
        opp_summary = html.Div([
            html.Strong("Stock sin venta no aplica con ‘Maneja stock = No’. "),
            html.Span("Esta oportunidad exige stock positivo y se calcula solo sobre SKU que manejan stock."),
        ], className="data-note")
    elif acciones_prod.empty:
        if hay_seccion and sinventa_total_tienda:
            opp_summary = html.Div([
                html.Strong("No hay SKU con stock sin venta en la sección seleccionada. "),
                html.Span(f"Con los mismos filtros hay {sinventa_total_tienda} en toda la tienda. Quita el filtro de rack/pasillo para verlos."),
            ], className="data-note")
        else:
            opp_summary = html.Div("No hay SKU con stock y sin venta para los filtros actuales.", className="empty-note")
    else:
        counts = acciones_prod["prioridad"].value_counts()
        opp_summary = html.Div([
            html.Div([html.Strong(int(counts.get("Alta", 0))), html.Span("prioridad alta")], className="mini-stat"),
            html.Div([html.Strong(int(counts.get("Media", 0))), html.Span("prioridad media")], className="mini-stat"),
            html.Div([html.Strong(sinventa_count), html.Span("en selección" if hay_seccion else "total sin venta")], className="mini-stat"),
            html.Div([html.Strong(sinventa_total_tienda), html.Span("total tienda")], className="mini-stat") if hay_seccion else None,
        ], className="mini-stat-grid opportunity-stats")
    sinventa_cols = [
        {"name": "Prioridad", "id": "prioridad"}, {"name": "SKU", "id": "cod_rapido"},
        {"name": "Producto", "id": "descripcion"}, {"name": "Categoría", "id": "categoria"},
        {"name": "Pasillo actual", "id": "pasillo"}, {"name": "Rack actual", "id": "rack"},
        {"name": "Marca", "id": "marca"}, {"name": "Stock hoy", "id": "stock", "type": "numeric", "format": NUM_FMT},
        {"name": "Venta AA", "id": "venta_anio_anterior", "type": "numeric", "format": MONEY_FMT},
        {"name": "Acción", "id": "accion"}, {"name": "Por qué", "id": "motivo"},
    ]
    comp_cards = _comparativo_cards(db.get_comparativo_anio(tienda))
    return (
        header, margen_status, kpis,
        fig_map, coord_status, fig_trend,
        clean_records(pasillos), cols_pasillo,
        clean_records(racks), cols_rack,
        clean_records(top), prod_cols,
        clean_records(baja), prod_cols,
        _figura_treemap(tree),
        opp_summary, clean_records(acciones_prod), sinventa_cols,
        comp_cards,
    )

# =========================
# Diagnóstico de selección
# =========================
@app.callback(
    Output("selection-panel", "children"),
    Input("store-seleccion", "data"), Input("f-tienda", "value"), Input("f-modo-periodo", "value"),
    Input("f-mes", "value"), Input("f-semana", "value"),
    Input("f-familia", "value"), Input("f-categoria", "value"), Input("f-clasificacion", "value"),
    Input("f-zona_pck", "value"), Input("f-responsable_linea", "value"), Input("f-marca", "value"),
    Input("f-maneja_stock", "value"), Input("f-pasillo-ubic", "value"), Input("f-rack-ubic", "value"),
)
def _selection_panel(seleccion, tienda, modo, mes, semana, familia, categoria, clasificacion,
                     zona_pck, responsable, marca, maneja, pasillo_ubic, rack_ubic):
    if not tienda:
        return None
    anio = db.get_anio_actual(tienda)
    filtros = make_filters(maneja, familia, categoria, clasificacion, zona_pck, responsable, marca)
    mes_sel = mes if modo == "Mes" else None
    semana_sel = semana if modo == "Semana" else None
    ctx = db.get_contexto_ubicacion_fisica(tienda, anio, mes_sel, semana_sel)
    fisico = bool(ctx.get("cubierto"))

    if not seleccion and (rack_ubic or pasillo_ubic):
        seleccion = {"nivel": "rack", "clave": str(rack_ubic)} if rack_ubic else {"nivel": "pasillo", "clave": str(pasillo_ubic)}

    if not seleccion:
        if fisico:
            acciones = db.get_acciones_rack_fisico(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros)
            accionables = acciones[acciones["accion"] != "Mantener"] if not acciones.empty else acciones
            if len(accionables):
                top = accionables.iloc[0]
                return html.Div([
                    html.Div("Sin sección seleccionada", className="diagnostic-kicker"),
                    html.H3("Empieza por la primera señal física", className="diagnostic-title"),
                    html.P(f"Rack {top['rack']}: {top['accion']}. {top['motivo']}", className="diagnostic-text"),
                    html.Div("Haz clic en el rack de la tabla o del plano para separar desempeño físico y surtido vigente.", className="diagnostic-hint"),
                ])
        return html.Div([
            html.Div("Sin sección seleccionada", className="diagnostic-kicker"),
            html.H3("Explora un rack", className="diagnostic-title"),
            html.P("Haz clic en el mapa. La ficha te indicará explícitamente si estás viendo historia física real o una vista atribuida al surtido actual.", className="diagnostic-text"),
        ])

    pasillo_f = seleccion["clave"] if seleccion.get("nivel") == "pasillo" else None
    rack_f = seleccion["clave"] if seleccion.get("nivel") == "rack" else None
    title = f"Rack {rack_f}" if rack_f else f"Pasillo {pasillo_f}"
    surtido = db.get_surtido_seccion(tienda, filtros=filtros, pasillo=pasillo_f, rack=rack_f)

    if fisico:
        resumen = db.get_resumen_fisico(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                        pasillo=pasillo_f, rack=rack_f) or {}
        basis = html.Div([
            html.Span("Desempeño físico", className="mode-pill mode-pill-good"),
            html.Span("La venta se asigna al rack/pasillo donde el SKU estaba realmente en la fecha de la venta."),
        ], className="diagnostic-basis diagnostic-basis-good")
        var_label = "Variación física"
        sold_label = "SKU vendidos aquí"
    else:
        resumen = db.get_resumen_periodo(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                         pasillo=pasillo_f, rack=rack_f)
        basis = html.Div([
            html.Span("Surtido actual", className="mode-pill"),
            html.Span("No existe ubicación física histórica completa para este período. La venta se atribuye al rack donde cada SKU está hoy."),
        ], className="diagnostic-basis")
        var_label = "Variación del surtido actual"
        sold_label = "SKU con venta del surtido"

    metrics = html.Div([
        html.Div([label_with_tip("Venta del período", "Con modo físico es la venta ocurrida realmente en esta sección. Con modo surtido actual es la venta de los SKU que hoy pertenecen a la sección."), html.Strong(fmt_money_short(resumen.get("venta")))]),
        html.Div([label_with_tip(var_label, "En modo físico compara contra el período inmediatamente anterior dentro de la ventana disponible. En modo surtido actual no representa necesariamente el mismo espacio físico histórico."), html.Strong(fmt_pct(resumen.get("variacion_pct")))]),
        html.Div([label_with_tip(sold_label, "Cantidad de códigos de producto distintos con venta en el universo mostrado."), html.Strong(f"{int(resumen.get('skus') or 0):,}".replace(",", "."))]),
        html.Div([label_with_tip("SKU asociados hoy", "Productos que el snapshot vigente ubica hoy en este rack/pasillo, hayan vendido o no."), html.Strong(f"{int(surtido.get('skus_asociados') or 0):,}".replace(",", "."))]),
        html.Div([label_with_tip("Unidades vendidas", "Cantidad vendida en el período y universo mostrado."), html.Strong(f"{float(resumen.get('unidades') or 0):,.0f}".replace(",", "."))]),
        html.Div([label_with_tip("SKU con stock hoy", "SKU del surtido vigente en la sección con stock disponible positivo ahora."), html.Strong(f"{int(surtido.get('skus_con_stock') or 0):,}".replace(",", "."))]),
    ], className="diagnostic-metrics")

    anomaly = None
    if fisico and int(resumen.get("skus") or 0) > 0 and int(surtido.get("skus_asociados") or 0) == 0:
        anomaly = html.Div([
            html.Strong("El rack cambió desde el período analizado."),
            html.Span(" Hay venta física histórica en este código, pero hoy no tiene SKU asociados. Puede ser cambio de planograma o codificación. No uses este caso para decidir el espacio actual sin validar."),
        ], className="data-warning")

    if resumen.get("periodo_anterior"):
        if fisico:
            comparison_note = html.Div([
                html.Strong("Comparación física: "),
                html.Span(f"período seleccionado vs {resumen.get('periodo_anterior')} ({fmt_money_short(resumen.get('venta_anterior'))}). No usa 2025 porque INFSTOCK no conserva esa ubicación."),
            ], className="diagnostic-comparison")
        else:
            comparison_note = html.Div([
                html.Strong("Comparación del surtido actual: "),
                html.Span(f"venta atribuida a los productos que hoy están aquí vs {resumen.get('periodo_anterior')} ({fmt_money_short(resumen.get('venta_anterior'))}). No significa que el rack físico tuviera el mismo surtido entonces."),
            ], className="diagnostic-comparison diagnostic-comparison-warn")
    else:
        comparison_note = html.Div("No existe un período comparable válido con este nivel de filtro.", className="diagnostic-comparison")

    action_box = None
    if fisico:
        acciones = db.get_acciones_rack_fisico(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros)
        if rack_f and not acciones.empty:
            match = acciones[acciones["rack"] == rack_f]
            if len(match):
                r = match.iloc[0]
                action_box = html.Div([
                    html.Div([html.Span(r["prioridad"], className=f"priority-pill priority-{str(r['prioridad']).lower()}"),
                              html.Span(label_with_tip("Recomendación física reciente", "Se basa en venta física del rack, posición relativa frente a otros racks y variación contra el período inmediatamente anterior. No usa margen ni comparación anual."), className="diagnostic-kicker")], className="action-hero-top"),
                    html.H4(r["accion"], className="diagnostic-action"),
                    html.P(r["motivo"], className="diagnostic-text"),
                    html.P(r["recomendacion"], className="diagnostic-recommendation"),
                ], className="diagnostic-action-box")
        elif pasillo_f and not acciones.empty:
            sub = acciones[(acciones["pasillo"] == pasillo_f) & (acciones["accion"] != "Mantener")]
            counts = sub["accion"].value_counts() if len(sub) else pd.Series(dtype=int)
            action_box = html.Div([
                html.Div("Señales físicas dentro del pasillo", className="diagnostic-kicker"),
                html.P(", ".join(f"{k}: {v}" for k, v in counts.items()) if len(counts) else "Sin señales urgentes en este pasillo.", className="diagnostic-text"),
            ], className="diagnostic-action-box")
    else:
        action_box = html.Div([
            html.Strong("No se emite recomendación de espacio para este período."),
            html.Span(" La composición del rack pudo ser distinta y no existe ubicación histórica suficiente para verificarlo."),
        ], className="data-warning")

    # 1) Qué está asociado HOY a la sección.
    curcats = db.get_categorias_surtido_actual(tienda, filtros=filtros, pasillo=pasillo_f, rack=rack_f, n=6)
    current_box = None
    if curcats is not None and not curcats.empty:
        rows = []
        for r in curcats.itertuples():
            rows.append(html.Div([
                html.Div([html.Strong(str(r.categoria), className="category-name"), html.Span(str(r.familia), className="category-family")]),
                html.Div([html.Strong(f"{int(r.skus_asociados)} SKU"), html.Span(f"{int(r.skus_con_stock)} con stock hoy")], className="category-values"),
            ], className="category-row"))
        current_box = html.Div([
            html.Div(label_with_tip("Surtido vigente hoy", "Composición actual del rack/pasillo según el último snapshot de INFSTOCK. No intenta reconstruir el surtido del período histórico."), className="diagnostic-kicker"),
            *rows,
        ], className="diagnostic-category-box")

    # 2) Qué categorías generaron venta en la sección durante el período.
    sold_box = None
    if fisico:
        soldcats = db.get_categorias_venta_fisica(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                                  pasillo=pasillo_f, rack=rack_f, n=6)
        sold_title = "Qué categorías vendieron físicamente aquí"
        sold_tip = "Venta realmente ocurrida en esta sección dentro de la ventana de ubicación histórica. La categoría usa la clasificación vigente del SKU."
    else:
        soldcats = db.get_categorias_seccion(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                             pasillo=pasillo_f, rack=rack_f, n=6)
        sold_title = "Venta de las categorías del surtido actual"
        sold_tip = "Fuera de la ventana física, suma la venta histórica de los SKU que hoy están asociados a esta sección. No representa necesariamente el surtido histórico del rack."
    if soldcats is not None and not soldcats.empty:
        rows = []
        for r in soldcats.itertuples():
            pct = float(getattr(r, "participacion_pct", 0) or 0)
            nsku = int(getattr(r, "skus_con_venta", 0) or 0)
            rows.append(html.Div([
                html.Div([html.Strong(str(r.categoria), className="category-name"), html.Span(str(r.familia), className="category-family")]),
                html.Div([html.Strong(fmt_money_short(r.venta)), html.Span(f"{pct:.1f}% venta · {nsku} SKU con venta")], className="category-values"),
            ], className="category-row"))
        sold_box = html.Div([
            html.Div(label_with_tip(sold_title, sold_tip), className="diagnostic-kicker"),
            *rows,
        ], className="diagnostic-category-box")

    return html.Div([
        html.Div("Sección seleccionada", className="diagnostic-kicker"),
        html.H3(title, className="diagnostic-title"),
        basis,
        anomaly,
        metrics,
        comparison_note,
        action_box,
        current_box,
        sold_box,
    ])

# =========================
# Descargas
# =========================
@app.callback(
    Output("download-detalle", "data"),
    Input("btn-download-detalle", "n_clicks"),
    State("f-tienda", "value"), State("f-modo-periodo", "value"), State("f-mes", "value"), State("f-semana", "value"),
    State("f-familia", "value"), State("f-categoria", "value"), State("f-clasificacion", "value"),
    State("f-zona_pck", "value"), State("f-responsable_linea", "value"), State("f-marca", "value"),
    State("f-maneja_stock", "value"), State("store-seleccion", "data"),
    State("f-pasillo-ubic", "value"), State("f-rack-ubic", "value"),
    prevent_initial_call=True,
)
def _descargar_detalle(_, tienda, modo, mes, semana, familia, categoria, clasificacion,
                       zona_pck, responsable, marca, maneja, seleccion, pasillo_ubic, rack_ubic):
    if not tienda:
        return dash.no_update
    anio = db.get_anio_actual(tienda)
    mes_sel = mes if modo == "Mes" else None
    semana_sel = semana if modo == "Semana" else None
    filtros = make_filters(maneja, familia, categoria, clasificacion, zona_pck, responsable, marca)
    pasillo_f, rack_f = effective_section(seleccion, pasillo_ubic, rack_ubic)
    ctx = db.get_contexto_ubicacion_fisica(tienda, anio, mes_sel, semana_sel)
    if (pasillo_f or rack_f) and ctx.get("cubierto"):
        df = db.get_detalle_productos_fisico(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                             pasillo=pasillo_f, rack=rack_f)
        modo_archivo = "fisico"
    else:
        df = db.get_detalle_productos(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                      pasillo=pasillo_f, rack=rack_f)
        modo_archivo = "surtido_actual"
    if df.empty:
        return dash.no_update
    sufijo = rack_f or pasillo_f or "tienda"
    return dcc.send_data_frame(df.to_csv, f"detalle_{modo_archivo}_{tienda}_{sufijo}_{anio}.csv",
                               index=False, sep=";", encoding="utf-8-sig")


@app.callback(
    Output("download-sinventa", "data"),
    Input("btn-download-sinventa", "n_clicks"),
    State("f-tienda", "value"), State("f-familia", "value"), State("f-categoria", "value"),
    State("f-clasificacion", "value"), State("f-zona_pck", "value"),
    State("f-responsable_linea", "value"), State("f-marca", "value"), State("f-maneja_stock", "value"),
    State("store-seleccion", "data"), State("f-pasillo-ubic", "value"), State("f-rack-ubic", "value"),
    prevent_initial_call=True,
)
def _descargar_sinventa(_, tienda, familia, categoria, clasificacion, zona_pck,
                        responsable, marca, maneja, seleccion, pasillo_ubic, rack_ubic):
    if not tienda or maneja == "No":
        return dash.no_update
    anio = db.get_anio_actual(tienda)
    filtros = make_filters(maneja, familia, categoria, clasificacion, zona_pck, responsable, marca)
    pasillo_f, rack_f = effective_section(seleccion, pasillo_ubic, rack_ubic)
    df = db.get_acciones_producto(tienda, anio, filtros=filtros, n=None, pasillo=pasillo_f, rack=rack_f)
    if df.empty:
        return dash.no_update
    sufijo = rack_f or pasillo_f or "tienda"
    return dcc.send_data_frame(df.to_csv, f"stock_sin_venta_{tienda}_{sufijo}_{anio}.csv",
                               index=False, sep=";", encoding="utf-8-sig")


# =========================
# Figuras
# =========================
def _figura_mapa(tienda, anio, mes_sel, semana_sel, filtros, nivel_mapa, seleccion, fisico=False):
    plano = db.get_plano(tienda)
    coords = db.get_coords(tienda, nivel=nivel_mapa)
    fig = go.Figure()
    if plano is None or coords.empty:
        fig.add_annotation(
            text=f"Todavía no hay plano/coordenadas de {nivel_mapa} para {tienda}. Súbelas en Administrar Planos.",
            showarrow=False, font=dict(size=14, color="#64748B"), x=0.5, y=0.5,
        )
        fig.update_xaxes(visible=False)
        fig.update_yaxes(visible=False)
        fig.update_layout(height=520, plot_bgcolor="#F8FAFC", paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=0, r=0, t=5, b=0))
        return fig

    venta_nivel = (db.get_venta_por_nivel_fisico(tienda, anio, mes=mes_sel, semana=semana_sel,
                                                  filtros=filtros, nivel=nivel_mapa)
                   if fisico else
                   db.get_venta_por_nivel(tienda, anio, mes=mes_sel, semana=semana_sel,
                                          filtros=filtros, nivel=nivel_mapa))
    venta_map = venta_nivel.set_index("clave")["venta"].to_dict() if len(venta_nivel) else {}
    coords = coords.copy()
    coords["venta"] = pd.to_numeric(coords["clave"].map(venta_map), errors="coerce").fillna(0.0)
    max_v = max(float(coords["venta"].max()), 1.0)
    positivos = coords.loc[coords["venta"] > 0, "venta"]
    tope_color = max(float(positivos.quantile(0.92)), 1.0) if len(positivos) else 1.0

    img_b64 = base64.b64encode(plano["imagen"]).decode("ascii")
    fig.add_layout_image(dict(
        source=f"data:image/png;base64,{img_b64}", xref="x", yref="y", x=0, y=0,
        sizex=plano["img_w"], sizey=plano["img_h"], sizing="stretch", layer="below", opacity=.82,
    ))

    # Cero venta = punto pequeño gris. Venta positiva = escala azul mucho más contrastada.
    cero = coords[coords["venta"] <= 0]
    if len(cero):
        fig.add_trace(go.Scatter(
            x=cero["x"], y=cero["y"], mode="markers", customdata=cero["clave"],
            marker=dict(size=6, color="#CBD5E1", line=dict(width=.7, color="white"), opacity=.65),
            text=[f"{nivel_mapa.capitalize()} {c}<br>Sin venta en el período" for c in cero["clave"]],
            hoverinfo="text", showlegend=False,
        ))

    pos = coords[coords["venta"] > 0]
    if len(pos):
        sizes = 11 + 27 * (pos["venta"] / max_v).pow(0.5)
        escala_fuerte = [[0.0, "#93C5FD"], [0.18, "#3B82F6"], [0.45, "#2563EB"],
                         [0.72, "#1D4ED8"], [1.0, "#172554"]]
        fig.add_trace(go.Scatter(
            x=pos["x"], y=pos["y"], mode="markers", customdata=pos["clave"],
            marker=dict(size=sizes, color=pos["venta"], colorscale=escala_fuerte, cmin=0, cmax=tope_color,
                        showscale=True, colorbar=dict(title="Venta", tickprefix="$", thickness=14, len=.60),
                        line=dict(width=1.5, color="white"), opacity=1),
            text=[f"{nivel_mapa.capitalize()} {c}<br>{'Venta física' if fisico else 'Venta atribuida al surtido actual'}<br><b>{fmt_money(v)}</b>" for c, v in zip(pos["clave"], pos["venta"])],
            hoverinfo="text", showlegend=False,
        ))

    if seleccion:
        sel_key = seleccion.get("clave")
        if seleccion.get("nivel") == "rack" and nivel_mapa == "pasillo" and sel_key:
            sel_key = str(sel_key)[:3]
        match = coords[coords["clave"].astype(str) == str(sel_key)] if sel_key else pd.DataFrame()
        if len(match):
            r = match.iloc[0]
            fig.add_trace(go.Scatter(
                x=[r["x"]], y=[r["y"]], mode="markers", hoverinfo="skip", showlegend=False,
                marker=dict(size=30, color="rgba(255,255,255,.10)", line=dict(width=4, color="#F59E0B")),
            ))

    fig.update_xaxes(visible=False, range=[0, plano["img_w"]], fixedrange=True)
    fig.update_yaxes(visible=False, range=[plano["img_h"], 0], fixedrange=True)
    fig.update_layout(
        height=560, margin=dict(l=0, r=0, t=5, b=0), plot_bgcolor="white",
        paper_bgcolor="rgba(0,0,0,0)", clickmode="event+select", showlegend=False,
    )
    return fig


def _figura_tendencia(df, semana_sel=None):
    fig = go.Figure()
    if df is None or df.empty:
        fig.add_annotation(text="Sin tendencia disponible", showarrow=False, font=dict(color="#64748B"))
        fig.update_xaxes(visible=False)
        fig.update_yaxes(visible=False)
        fig.update_layout(height=220, margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor="rgba(0,0,0,0)")
        return fig
    df = df.copy()
    df["venta"] = pd.to_numeric(df["venta"], errors="coerce").fillna(0)
    fig.add_trace(go.Scatter(
        x=df["semana"], y=df["venta"], mode="lines+markers",
        line=dict(color="#2563EB", width=2.5), marker=dict(size=5, color="#2563EB"),
        fill="tozeroy", fillcolor="rgba(37,99,235,.08)",
        hovertemplate="Semana %{x}<br><b>$%{y:,.0f}</b><extra></extra>",
    ))
    if semana_sel:
        fig.add_vline(x=semana_sel, line_width=1.5, line_dash="dot", line_color="#F59E0B")
    fig.update_layout(
        height=220, margin=dict(l=4, r=4, t=8, b=26), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="white",
        hovermode="x unified",
    )
    fig.update_xaxes(title=None, showgrid=False, tickfont=dict(size=10, color="#64748B"))
    fig.update_yaxes(title=None, showgrid=True, gridcolor="#EEF2F7", zeroline=False,
                     tickfont=dict(size=10, color="#64748B"), tickprefix="$", tickformat="~s")
    return fig


def _figura_treemap(tree):
    fig = go.Figure()
    if tree is None or tree.empty:
        fig.add_annotation(text="Sin datos para esta selección", showarrow=False, font=dict(color="#64748B"))
        fig.update_layout(height=470, margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor="rgba(0,0,0,0)")
        return fig

    tree = tree.copy()
    # Responsable/Jefe de línea no existe en el origen actual. Si toda la columna
    # viene vacía, evitamos mostrar un nivel artificial '(sin jefe de línea)'.
    tiene_jefe = ("jefe_linea" in tree.columns and
                  tree["jefe_linea"].fillna("").astype(str).str.strip().replace("(sin jefe de línea)", "").ne("").any())
    familias = tree.groupby("familia", as_index=False)["venta"].sum()
    if tiene_jefe:
        jefes = tree.groupby(["familia", "jefe_linea"], as_index=False)["venta"].sum()
        ids = (list(familias["familia"]) +
               [f"{r.familia}|{r.jefe_linea}" for r in jefes.itertuples()] +
               [f"{r.familia}|{r.jefe_linea}|{r.categoria}" for r in tree.itertuples()])
        labels = list(familias["familia"]) + list(jefes["jefe_linea"]) + list(tree["categoria"])
        parents = ([""] * len(familias) + list(jefes["familia"]) +
                   [f"{r.familia}|{r.jefe_linea}" for r in tree.itertuples()])
        values = list(familias["venta"]) + list(jefes["venta"]) + list(tree["venta"])
    else:
        cats = tree.groupby(["familia", "categoria"], as_index=False)["venta"].sum()
        ids = list(familias["familia"]) + [f"{r.familia}|{r.categoria}" for r in cats.itertuples()]
        labels = list(familias["familia"]) + list(cats["categoria"])
        parents = [""] * len(familias) + list(cats["familia"])
        values = list(familias["venta"]) + list(cats["venta"])

    fig.add_trace(go.Treemap(
        ids=ids, labels=labels, parents=parents, values=values, branchvalues="total",
        marker=dict(colors=values, colorscale=[[0,"#DBEAFE"],[.45,"#3B82F6"],[1,"#1E3A8A"]], line=dict(width=1, color="white")),
        texttemplate="%{label}<br>$%{value:,.0f}", hovertemplate="%{label}<br><b>$%{value:,.0f}</b><extra></extra>",
    ))
    fig.update_layout(height=500, margin=dict(l=0, r=0, t=8, b=0), paper_bgcolor="rgba(0,0,0,0)")
    return fig


# =========================
# Comparativo anual
# =========================
def _comparativo_cards(comp):
    if comp is None or len(comp) < 2:
        return html.Div("Sin datos suficientes para comparar años.", className="empty-note")
    comp = comp.sort_values("anio")
    a_actual, a_ant = comp.iloc[-1], comp.iloc[-2]

    def _var(actual, anterior):
        if anterior is None or pd.isna(anterior) or float(anterior) == 0:
            return None
        return (float(actual) - float(anterior)) / float(anterior) * 100

    filas = [
        ("Venta", fmt_money_short(a_actual.venta), _var(a_actual.venta, a_ant.venta), fmt_money_short(a_ant.venta)),
        ("Transacciones", f"{int(a_actual.trx):,}".replace(",", "."), _var(a_actual.trx, a_ant.trx),
         f"{int(a_ant.trx):,}".replace(",", ".")),
        ("Ticket promedio", fmt_money(a_actual.ticket_promedio), _var(a_actual.ticket_promedio, a_ant.ticket_promedio),
         fmt_money(a_ant.ticket_promedio)),
        ("Clientes únicos", f"{int(a_actual.clientes):,}".replace(",", "."), _var(a_actual.clientes, a_ant.clientes),
         f"{int(a_ant.clientes):,}".replace(",", ".")),
    ]
    return html.Div([
        metric_card(nombre, valor,
                    f"{fmt_pct(var)} vs {int(a_ant.anio)} · {ant}",
                    "good" if var is not None and var >= 0 else "bad" if var is not None else "muted")
        for nombre, valor, var, ant in filas
    ], className="metric-grid annual-grid")


# =========================
# Cross-sell
# =========================
def _cross_signal(conf, lift):
    if pd.isna(conf) or pd.isna(lift):
        return "—"
    frecuente = float(conf) >= 0.15
    especifica = float(lift) >= 5
    if frecuente and especifica:
        return "Frecuente + específica"
    if frecuente:
        return "Frecuente"
    if especifica:
        return "Afinidad específica"
    return "Explorar"

@app.callback(Output("tabla-combos", "data"), Output("tabla-combos", "columns"),
              Input("f-tienda", "value"), Input("combo-orden", "value"), Input("store-seleccion", "data"),
              Input("f-pasillo-ubic", "value"), Input("f-rack-ubic", "value"))
def _combos_top(tienda, orden, seleccion, pasillo_ubic, rack_ubic):
    if not tienda:
        return [], []
    pasillo_f, rack_f = effective_section(seleccion, pasillo_ubic, rack_ubic)
    df = db.get_top_combos(tienda, n=40, orden=orden, pasillo=pasillo_f, rack=rack_f)
    if not df.empty:
        df = df.copy()
        conf = pd.to_numeric(df["confianza_a_b"], errors="coerce")
        lift_num = pd.to_numeric(df["lift"], errors="coerce")
        boletas = pd.to_numeric(df["boletas"], errors="coerce")
        df["confianza_pct"] = conf * 100
        df["frecuencia_base_pct"] = (conf / lift_num.replace(0, pd.NA)) * 100
        df["oportunidad_sin_b"] = ((boletas / conf.replace(0, pd.NA)) - boletas).round()
        df["afinidad_txt"] = lift_num.apply(lambda x: f"{x:.1f}×".replace(".", ",") if pd.notna(x) else "—")
        df["senal"] = [_cross_signal(c, l) for c, l in zip(conf, lift_num)]
    cols = [
        {"name": "Producto A", "id": "desc_a"}, {"name": "Producto B", "id": "desc_b"},
        {"name": "Compras juntas", "id": "boletas", "type": "numeric", "format": NUM_FMT},
        {"name": "% A → B", "id": "confianza_pct", "type": "numeric", "format": PCT_FMT},
        {"name": "Frecuencia normal de B", "id": "frecuencia_base_pct", "type": "numeric", "format": PCT_FMT},
        {"name": "Afinidad", "id": "afinidad_txt"},
        {"name": "A sin B", "id": "oportunidad_sin_b", "type": "numeric", "format": NUM_FMT},
        {"name": "Lectura", "id": "senal"},
    ]
    return clean_records(df), cols


@app.callback(Output("combo-producto", "options"), Input("f-tienda", "value"),
              Input("store-seleccion", "data"), Input("f-pasillo-ubic", "value"), Input("f-rack-ubic", "value"))
def _combo_producto_opts(tienda, seleccion, pasillo_ubic, rack_ubic):
    if not tienda:
        return []
    pasillo_f, rack_f = effective_section(seleccion, pasillo_ubic, rack_ubic)
    df = db.get_productos_lista(tienda, pasillo=pasillo_f, rack=rack_f)
    return [{"label": r.descripcion, "value": r.sku} for r in df.itertuples()]


@app.callback(Output("tabla-combos-producto", "data"), Output("tabla-combos-producto", "columns"),
              Input("f-tienda", "value"), Input("combo-producto", "value"))
def _combos_producto(tienda, sku):
    if not tienda or not sku:
        return [], []
    df = db.get_combos_de_producto(tienda, sku, n=15)
    if not df.empty:
        df = df.copy()
        conf = pd.to_numeric(df["confianza"], errors="coerce")
        lift_num = pd.to_numeric(df["lift"], errors="coerce")
        df["confianza_pct"] = conf * 100
        df["frecuencia_base_pct"] = pd.to_numeric(df.get("frecuencia_base"), errors="coerce") * 100
        df["afinidad_txt"] = lift_num.apply(lambda x: f"{x:.1f}×".replace(".", ",") if pd.notna(x) else "—")
        df["senal"] = [_cross_signal(c, l) for c, l in zip(conf, lift_num)]
    cols = [
        {"name": "Complemento sugerido", "id": "producto"},
        {"name": "Compras juntas", "id": "boletas", "type": "numeric", "format": NUM_FMT},
        {"name": "% que también lo llevan", "id": "confianza_pct", "type": "numeric", "format": PCT_FMT},
        {"name": "Frecuencia normal", "id": "frecuencia_base_pct", "type": "numeric", "format": PCT_FMT},
        {"name": "Afinidad", "id": "afinidad_txt"},
        {"name": "Compras sin complemento", "id": "oportunidades_sin_complemento", "type": "numeric", "format": NUM_FMT},
        {"name": "Lectura", "id": "senal"},
    ]
    return clean_records(df), cols


# =========================
# Administrar planos
# =========================
@app.callback(Output("admin-tienda", "options"), Output("admin-tienda", "value"), Input("url", "pathname"))
def _admin_init(pathname):
    if pathname != "/admin":
        return dash.no_update, dash.no_update
    _lazy_init()
    tiendas = db.get_tiendas()
    con_plano = set(db.tiendas_con_plano())
    opts = [{"label": f"{r.cod_tienda} {'· plano cargado' if r.cod_tienda in con_plano else ''}", "value": r.cod_tienda}
            for r in tiendas.itertuples()]
    return opts, ("SANRO" if "SANRO" in tiendas["cod_tienda"].tolist() else (tiendas["cod_tienda"].iloc[0] if len(tiendas) else None))


@app.callback(Output("admin-imagen-preview", "children"), Output("admin-imagen-store", "data"),
              Input("admin-upload-imagen", "contents"))
def _admin_preview_imagen(contents):
    if not contents:
        return None, None
    return html.Img(src=contents, className="admin-preview"), contents


@app.callback(Output("admin-plano-msg", "children"),
              Input("admin-guardar-plano", "n_clicks"),
              State("admin-imagen-store", "data"), State("admin-tienda", "value"), prevent_initial_call=True)
def _admin_guardar_plano(n, contents, tienda):
    if not contents or not tienda:
        return dbc.Alert("Primero sube una imagen.", color="warning")
    from PIL import Image
    import io as _io
    _, b64data = contents.split(",", 1)
    raw = base64.b64decode(b64data)
    img = Image.open(_io.BytesIO(raw)).convert("RGB")
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    db.guardar_plano(tienda, buf.getvalue(), img.width, img.height)
    return dbc.Alert(f"Plano de {tienda} guardado ({img.width}×{img.height}px).", color="success")


@app.callback(Output("admin-coords-msg", "children"),
              Input("admin-guardar-coords", "n_clicks"),
              State("admin-upload-csv", "contents"), State("admin-tienda", "value"), State("admin-nivel", "value"),
              prevent_initial_call=True)
def _admin_guardar_coords(n, contents, tienda, nivel):
    if not contents or not tienda:
        return dbc.Alert("Primero sube un CSV.", color="warning")
    import io as _io
    _, b64data = contents.split(",", 1)
    raw = base64.b64decode(b64data)
    df = pd.read_csv(_io.BytesIO(raw))
    df.columns = [c.lower() for c in df.columns]
    nivel_key = "pasillo" if nivel == "Pasillo" else "rack"
    if {nivel_key, "x", "y"} - set(df.columns):
        return dbc.Alert(f"El CSV necesita columnas: {nivel_key}, x, y.", color="danger")
    db.guardar_coords(tienda, df, nivel=nivel_key)
    return dbc.Alert(f"{len(df)} filas de {nivel_key} guardadas para {tienda}.", color="success")


@app.callback(
    Output("admin-tabla-pasillo", "data"), Output("admin-tabla-pasillo", "columns"),
    Output("admin-tabla-rack", "data"), Output("admin-tabla-rack", "columns"),
    Input("admin-tienda", "value"), Input("admin-plano-msg", "children"), Input("admin-coords-msg", "children"),
)
def _admin_tablas(tienda, *_):
    if not tienda:
        return [], [], [], []
    cp = db.get_coords(tienda, "pasillo")
    cr = db.get_coords(tienda, "rack")
    cols = [{"name": n, "id": c} for n, c in [("Clave", "clave"), ("X", "x"), ("Y", "y")]]
    return clean_records(cp), cols, clean_records(cr), cols


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 8050)))
