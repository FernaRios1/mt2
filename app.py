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
    locale_format={"group": ".", "decimal": ","},
)

ACTION_STYLE = [
    {"if": {"filter_query": '{prioridad} = "Alta"', "column_id": "prioridad"},
     "backgroundColor": "#FEE2E2", "color": "#991B1B", "fontWeight": "700"},
    {"if": {"filter_query": '{prioridad} = "Media"', "column_id": "prioridad"},
     "backgroundColor": "#FEF3C7", "color": "#92400E", "fontWeight": "700"},
    {"if": {"filter_query": '{accion} = "Potenciar rack"', "column_id": "accion"},
     "color": "#047857", "fontWeight": "700"},
    {"if": {"filter_query": '{accion} = "Proteger venta"', "column_id": "accion"},
     "color": "#B45309", "fontWeight": "700"},
    {"if": {"filter_query": '{accion} = "Revisar rack"', "column_id": "accion"},
     "color": "#B91C1C", "fontWeight": "700"},
    {"if": {"state": "active"}, "backgroundColor": "#FFF7ED", "border": "1px solid #FDBA74"},
]


def section(title, children, subtitle=None, class_name="section-panel"):
    head = [html.Div([
        html.H2(title, className="section-title"),
        html.Div(subtitle, className="section-subtitle") if subtitle else None,
    ], className="section-head")]
    return html.Section([*head, *children], className=class_name)


def metric_card(label, value, helper=None, tone="default"):
    return html.Div([
        html.Div(label, className="metric-label"),
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


# =========================
# Layout principal
# =========================
sidebar = html.Aside([
    html.Div([
        html.Div("IMPERIAL", className="brand-eyebrow"),
        html.Div("Desempeño de Racks", className="brand-mark"),
        html.Div("Decisiones de espacio y surtido", className="brand-sub"),
    ], className="brand"),

    html.Div("Tienda", className="side-label"),
    dcc.Dropdown(id="f-tienda", clearable=False, className="side-dd"),

    html.Div("Período", className="side-label side-gap"),
    dcc.RadioItems(
        id="f-modo-periodo",
        options=[{"label": "Mes", "value": "Mes"}, {"label": "Semana", "value": "Semana"},
                 {"label": "Año", "value": "Año completo"}],
        value="Mes", className="period-segment", labelClassName="period-option",
        inputClassName="period-input",
    ),
    dcc.Dropdown(id="f-mes", clearable=False, className="side-dd"),
    dcc.Dropdown(id="f-semana", clearable=False, className="side-dd", style={"display": "none"}),

    html.Details([
        html.Summary("Filtros avanzados", className="filters-summary"),
        html.Div([
            dcc.Dropdown(id="f-familia", multi=True, placeholder="Familia", className="side-dd"),
            dcc.Dropdown(id="f-categoria", multi=True, placeholder="Categoría", className="side-dd"),
            dcc.Dropdown(id="f-clasificacion", multi=True, placeholder="Clasificación SKU", className="side-dd"),
            dcc.Dropdown(id="f-zona_pck", multi=True, placeholder="Zona de picking", className="side-dd"),
            dcc.Dropdown(id="f-responsable_linea", multi=True, placeholder="Jefe de línea", className="side-dd"),
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
    html.Div("Las recomendaciones usan venta, tendencia y surtido. El margen se incorporará cuando esté disponible.",
             className="side-foot"),
], className="sidebar")


action_table = dash_table.DataTable(
    id="tabla-acciones-rack",
    **BASE_TABLE,
    style_data_conditional=ACTION_STYLE,
    cell_selectable=True,
)

main = html.Main([
    html.Div(id="header-tienda"),
    html.Div(id="margen-status"),
    html.Div(id="kpi-row", className="metric-grid"),

    section("Qué hacer primero", [
        html.Div(id="action-summary"),
        html.Div("Haz clic en una fila para analizar ese rack.", className="micro-help"),
        action_table,
    ], subtitle="Respeta el período y los filtros activos. Cada acción muestra la señal que la dispara."),

    dbc.Row([
        dbc.Col(section("Mapa de la tienda", [
            html.Div([
                dbc.RadioItems(id="f-nivel-mapa", options=["Pasillo", "Rack"], value="Rack",
                               inline=True, className="nivel-toggle"),
                html.Div(id="coord-status"),
            ], className="map-toolbar"),
            dcc.Graph(id="mapa-calor", config={"displayModeBar": False, "scrollZoom": False}),
        ], subtitle="El tamaño y color muestran venta del período. Haz clic para abrir el diagnóstico de una sección."), md=8),
        dbc.Col([
            section("Diagnóstico", [
                html.Div(id="selection-panel"),
                html.Button("Quitar selección", id="btn-clear-selection", n_clicks=0, className="ghost-button"),
            ], subtitle="KPIs, categorías y recomendación respetan el período y los filtros activos."),
            section("Tendencia semanal", [
                dcc.Graph(id="tendencia-semanal", config={"displayModeBar": False}),
            ], class_name="section-panel compact-panel"),
        ], md=4),
    ], className="g-3"),

    section("Explorar el porqué", [
        html.Div([
            html.Div("El detalle de estas pestañas respeta tienda, período, filtros y rack/pasillo seleccionado.", className="micro-help"),
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
                        dash_table.DataTable(id="tabla-racks", **BASE_TABLE),
                    ], md=7),
                    dbc.Col([
                        html.Div("Pasillos", className="subblock-title"),
                        dash_table.DataTable(id="tabla-pasillos", **BASE_TABLE),
                    ], md=5),
                ], className="g-3 tab-content"),
            ]),
            dcc.Tab(label="Productos", value="productos", className="detail-tab", selected_className="detail-tab-selected", children=[
                dbc.Row([
                    dbc.Col([
                        html.Div("Productos que explican la venta", className="subblock-title"),
                        dash_table.DataTable(id="tabla-top", **BASE_TABLE),
                    ], md=6),
                    dbc.Col([
                        html.Div("Baja contribución con venta", className="subblock-title"),
                        dash_table.DataTable(id="tabla-baja", **BASE_TABLE),
                    ], md=6),
                ], className="g-3 tab-content"),
            ]),
            dcc.Tab(label="Categorías", value="categorias", className="detail-tab", selected_className="detail-tab-selected", children=[
                html.Div([
                    html.Div("Familia → jefe de línea → categoría", className="subblock-title"),
                    dcc.Graph(id="treemap-familia", config={"displayModeBar": False}),
                ], className="tab-content"),
            ]),
            dcc.Tab(label="Oportunidades", value="oportunidades", className="detail-tab", selected_className="detail-tab-selected", children=[
                html.Div(className="tab-content", children=[
                    html.Div(id="oportunidades-resumen"),
                    html.Div("SKU con stock y sin venta", className="subblock-title"),
                    dash_table.DataTable(id="tabla-sinventa", **BASE_TABLE,
                                         style_data_conditional=ACTION_STYLE),
                    html.Hr(className="soft-hr"),
                    html.Div("Cross-sell y combos", className="subblock-title"),
                    html.Div("Cross-sell se calcula con las boletas del año. Respeta la tienda y la sección seleccionada; el período mes/semana no recalcula los pares.",
                             className="section-subtitle"),
                    dbc.Row([
                        dbc.Col([
                            dcc.Dropdown(id="combo-orden",
                                         options=[{"label": "Más frecuentes", "value": "boletas"},
                                                  {"label": "Mayor lift", "value": "lift"},
                                                  {"label": "Mayor confianza", "value": "confianza"}],
                                         value="boletas", clearable=False),
                            dash_table.DataTable(id="tabla-combos", **BASE_TABLE),
                        ], md=6),
                        dbc.Col([
                            dcc.Dropdown(id="combo-producto", placeholder="Buscar producto para ver qué se compra junto…"),
                            dash_table.DataTable(id="tabla-combos-producto", **BASE_TABLE),
                        ], md=6),
                    ], className="g-3"),
                ]),
            ]),
        ]),
    ], subtitle="El clic del mapa o de la cola de acciones filtra el detalle para explicar qué está pasando."),

    section("Contexto anual", [
        html.Div(id="comparativo-cards"),
    ], subtitle="Contexto de tienda completa. No cambia con filtros de producto ni con el clic en un rack."),

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
    Output("f-maneja_stock", "value"),
    Input("btn-clear-filters", "n_clicks"), prevent_initial_call=True,
)
def _limpiar_filtros(_):
    return [], [], [], [], [], [], "Todos"


@app.callback(
    Output("active-filter-summary", "children"),
    Input("f-familia", "value"), Input("f-categoria", "value"), Input("f-clasificacion", "value"),
    Input("f-zona_pck", "value"), Input("f-responsable_linea", "value"), Input("f-marca", "value"),
    Input("f-maneja_stock", "value"),
)
def _resumen_filtros(familia, categoria, clasificacion, zona_pck, responsable, marca, maneja):
    items = []
    etiquetas = [("Familia", familia), ("Categoría", categoria), ("Clasificación", clasificacion),
                 ("Zona", zona_pck), ("Jefe", responsable), ("Marca", marca)]
    for label, vals in etiquetas:
        if vals:
            items.append(f"{label}: {', '.join(map(str, vals[:2]))}{'…' if len(vals) > 2 else ''}")
    if maneja and maneja != "Todos":
        items.append(f"Maneja stock: {maneja}")
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
    State("tabla-acciones-rack", "data"), State("store-seleccion", "data"),
    prevent_initial_call=True,
)
def _seleccionar(click_mapa, action_cell, clear_clicks, tienda, mes, semana, nivel,
                  familia, categoria, clasificacion, zona_pck, responsable, marca, maneja,
                  action_data, actual):
    trig = callback_context.triggered_id
    reset_ids = {"f-tienda", "f-mes", "f-semana", "f-nivel-mapa", "f-familia", "f-categoria",
                 "f-clasificacion", "f-zona_pck", "f-responsable_linea", "f-marca", "f-maneja_stock"}
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
)
def _acciones_rack(tienda, modo, mes, semana, familia, categoria, clasificacion, zona_pck, responsable, marca, maneja):
    if not tienda:
        return None, [], []
    anio = db.get_anio_actual(tienda)
    filtros = make_filters(maneja, familia, categoria, clasificacion, zona_pck, responsable, marca)
    mes_sel = mes if modo == "Mes" else None
    semana_sel = semana if modo == "Semana" else None
    df = db.get_acciones_rack(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros)
    if df.empty:
        return html.Div("No hay datos para construir recomendaciones."), [], []

    accionables = df[df["accion"] != "Mantener"].copy()
    alta = int((df["prioridad"] == "Alta").sum())
    proteger = int((df["accion"] == "Proteger venta").sum())
    revisar = int((df["accion"] == "Revisar rack").sum())
    potenciar = int((df["accion"] == "Potenciar rack").sum())
    optimizar = int((df["accion"] == "Optimizar surtido").sum())

    top = accionables.iloc[0] if len(accionables) else df.iloc[0]
    comp_label = None
    if "comparacion_label" in df.columns and len(df):
        comp_label = df.iloc[0].get("comparacion_label")
    periodo_actual = (f"Semana {semana_sel}" if semana_sel else
                      MONTHS.get(int(mes_sel), f"Mes {mes_sel}") if mes_sel else f"Año {anio}")
    comparacion_note = (f"Acciones calculadas para {periodo_actual}. Comparación: {comp_label}."
                        if comp_label else
                        f"Acciones calculadas para {periodo_actual}. Sin período comparable disponible; se usa nivel de venta y venta por SKU.")

    summary = html.Div([
        html.Div([
            html.Div([html.Span(top["prioridad"], className=f"priority-pill priority-{top['prioridad'].lower()}"),
                      html.Span(f"Rack {top['rack']} · Pasillo {top['pasillo']}", className="action-location")],
                     className="action-hero-top"),
            html.H3(top["accion"], className="action-hero-title"),
            html.P(top["motivo"], className="action-hero-text"),
            html.P(top["recomendacion"], className="action-hero-reco"),
        ], className="action-hero"),
        html.Div([
            html.Div([html.Strong(alta), html.Span("prioridad alta")], className="mini-stat"),
            html.Div([html.Strong(proteger), html.Span("proteger")], className="mini-stat"),
            html.Div([html.Strong(revisar), html.Span("revisar")], className="mini-stat"),
            html.Div([html.Strong(potenciar), html.Span("potenciar")], className="mini-stat"),
            html.Div([html.Strong(optimizar), html.Span("optimizar")], className="mini-stat"),
        ], className="mini-stat-grid"),
        html.Div(comparacion_note, className="engine-note"),
    ], className="action-summary")

    view = accionables[["prioridad", "accion", "pasillo", "rack", "venta", "variacion_pct",
                        "skus", "venta_por_sku", "motivo"]].head(250).copy()
    view["id"] = view["rack"].astype(str)
    cols = [
        {"name": "Prioridad", "id": "prioridad"},
        {"name": "Acción", "id": "accion"},
        {"name": "Pasillo", "id": "pasillo"},
        {"name": "Rack", "id": "rack"},
        {"name": "Venta período", "id": "venta", "type": "numeric", "format": MONEY_FMT},
        {"name": "Variación", "id": "variacion_pct", "type": "numeric", "format": PCT_FMT},
        {"name": "SKUs", "id": "skus", "type": "numeric", "format": NUM_FMT},
        {"name": "Venta / SKU", "id": "venta_por_sku", "type": "numeric", "format": MONEY_FMT},
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
)
def _actualizar(tienda, modo, mes, semana, familia, categoria, clasificacion, zona_pck,
                 responsable_linea, marca, maneja_sel, nivel_mapa_lbl, seleccion):
    if not tienda:
        return [dash.no_update] * 19

    anio = db.get_anio_actual(tienda)
    if anio is None:
        return [dash.no_update] * 19
    mes_sel = mes if modo == "Mes" else None
    semana_sel = semana if modo == "Semana" else None
    filtros = make_filters(maneja_sel, familia, categoria, clasificacion, zona_pck, responsable_linea, marca)
    nivel_mapa = "pasillo" if nivel_mapa_lbl == "Pasillo" else "rack"

    pasillo_f = seleccion["clave"] if seleccion and seleccion.get("nivel") == "pasillo" else None
    rack_f = seleccion["clave"] if seleccion and seleccion.get("nivel") == "rack" else None

    resumen = db.get_resumen_periodo(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                     pasillo=pasillo_f, rack=rack_f)
    pasillos = db.get_pasillo_resumen(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                      pasillo=pasillo_f, rack=rack_f)
    racks = db.get_rack_detalle(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                pasillo=pasillo_f, rack=rack_f)
    top = db.get_top_productos(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                               pasillo=pasillo_f, rack=rack_f, n=50)
    baja = db.get_top_productos(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                pasillo=pasillo_f, rack=rack_f, n=50, ascendente=True)
    tree = db.get_treemap(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                          pasillo=pasillo_f, rack=rack_f)
    sinventa_count = db.get_sin_venta_count(tienda, anio, filtros=filtros, pasillo=pasillo_f, rack=rack_f)
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

    selection_txt = None
    if rack_f:
        selection_txt = f"Analizando rack {rack_f}"
    elif pasillo_f:
        selection_txt = f"Analizando pasillo {pasillo_f}"

    sync = db.get_sync_status()
    sync_txt = "Datos cargados desde Postgres"
    if sync and sync.get("ejecutado_en") is not None:
        ts = pd.to_datetime(sync["ejecutado_en"], errors="coerce")
        if pd.notna(ts):
            sync_txt = f"Última sincronización: {ts.strftime('%d/%m/%Y %H:%M')}"
            msg = sync.get("mensaje")
            if msg and str(msg) != "OK":
                sync_txt += f" · {msg}"

    n_filtros = sum(len(v) for v in filtros.values())
    header = html.Div([
        html.Div([
            html.Div("ANÁLISIS DE ESPACIO", className="page-eyebrow"),
            html.H1(f"{tienda} · {nombre}", className="page-title"),
            html.Div([
                html.Span(tipo, className="context-chip") if tipo else None,
                html.Span(periodo_txt, className="context-chip"),
                html.Span(f"{n_filtros} filtros activos", className="context-chip") if n_filtros else None,
                html.Span(selection_txt, className="context-chip context-selected") if selection_txt else None,
            ], className="context-row"),
        ]),
        html.Div(sync_txt, className="sync-text"),
    ], className="page-header")

    if float(resumen.get("margen") or 0) == 0:
        margen_status = html.Div([
            html.Span("Modo desempeño", className="mode-pill"),
            html.Span("Margen pendiente: las recomendaciones actuales usan venta, tendencia y surtido; no se presenta falsa rentabilidad."),
        ], className="mode-banner")
    else:
        margen_status = html.Div([
            html.Span("Rentabilidad activa", className="mode-pill mode-pill-good"),
            html.Span("La base ya contiene margen y puede incorporarse al motor de decisión."),
        ], className="mode-banner")

    var = resumen.get("variacion_pct")
    var_tone = "good" if var is not None and var >= 0 else "bad" if var is not None else "muted"
    prev_helper = (f"vs {resumen.get('periodo_anterior')}: {fmt_money_short(resumen.get('venta_anterior'))}"
                   if resumen.get("periodo_anterior") else "Sin período comparable con este nivel de filtro")
    kpis = [
        metric_card("Venta del período", fmt_money_short(resumen.get("venta")),
                    "Respeta todos los filtros y la selección del mapa"),
        metric_card("Variación", fmt_pct(var), prev_helper, var_tone),
        metric_card("Racks con venta", f"{int(resumen.get('racks') or 0):,}".replace(",", "."),
                    f"{int(resumen.get('skus') or 0):,} SKU con venta".replace(",", ".")),
        metric_card("Stock sin venta YTD", f"{sinventa_count:,}".replace(",", "."),
                    "SKU con stock positivo y sin venta en el año", "bad" if sinventa_count else "good"),
    ]

    fig_map = _figura_mapa(tienda, anio, mes_sel, semana_sel, filtros, nivel_mapa, seleccion)
    sin_coord = db.get_sin_coordenadas(tienda, nivel=nivel_mapa)
    if len(sin_coord):
        coord_status = html.Span(f"{len(sin_coord)} sin coordenada", className="coord-warning")
    else:
        coord_status = html.Span("Plano completo", className="coord-ok")

    tendencia = db.get_tendencia_semana(tienda, anio, filtros=filtros, pasillo=pasillo_f, rack=rack_f)
    fig_trend = _figura_tendencia(tendencia, semana_sel)

    if len(pasillos):
        pasillos = pasillos.copy()
        pasillos["venta_por_rack"] = pasillos["venta"] / pasillos["racks"].replace(0, pd.NA)
    if len(racks):
        racks = racks.copy()
        racks["venta_por_sku"] = racks["venta"] / racks["skus"].replace(0, pd.NA)

    cols_pasillo = [
        {"name": "Pasillo", "id": "pasillo"},
        {"name": "Racks", "id": "racks", "type": "numeric", "format": NUM_FMT},
        {"name": "SKUs", "id": "skus", "type": "numeric", "format": NUM_FMT},
        {"name": "Venta", "id": "venta", "type": "numeric", "format": MONEY_FMT},
        {"name": "Venta / rack", "id": "venta_por_rack", "type": "numeric", "format": MONEY_FMT},
    ]
    cols_rack = [
        {"name": "Pasillo", "id": "pasillo"}, {"name": "Rack", "id": "rack"},
        {"name": "SKUs", "id": "skus", "type": "numeric", "format": NUM_FMT},
        {"name": "Venta", "id": "venta", "type": "numeric", "format": MONEY_FMT},
        {"name": "Venta / SKU", "id": "venta_por_sku", "type": "numeric", "format": MONEY_FMT},
        {"name": "Unidades", "id": "unidades", "type": "numeric", "format": NUM_FMT},
    ]
    prod_cols = [
        {"name": "SKU", "id": "cod_rapido"}, {"name": "Producto", "id": "descripcion"},
        {"name": "Categoría", "id": "categoria"}, {"name": "Familia", "id": "familia"},
        {"name": "Marca", "id": "marca"}, {"name": "Stock", "id": "stock", "type": "numeric", "format": NUM_FMT},
        {"name": "Venta", "id": "venta", "type": "numeric", "format": MONEY_FMT},
        {"name": "Cantidad", "id": "cantidad", "type": "numeric", "format": NUM_FMT},
    ]

    if acciones_prod.empty:
        opp_summary = html.Div("No hay SKU con stock y sin venta para los filtros actuales.", className="empty-note")
    else:
        counts = acciones_prod["prioridad"].value_counts()
        opp_summary = html.Div([
            html.Div([html.Strong(int(counts.get("Alta", 0))), html.Span("prioridad alta")], className="mini-stat"),
            html.Div([html.Strong(int(counts.get("Media", 0))), html.Span("prioridad media")], className="mini-stat"),
            html.Div([html.Strong(sinventa_count), html.Span("total sin venta")], className="mini-stat"),
        ], className="mini-stat-grid opportunity-stats")

    sinventa_cols = [
        {"name": "Prioridad", "id": "prioridad"}, {"name": "SKU", "id": "cod_rapido"},
        {"name": "Producto", "id": "descripcion"}, {"name": "Categoría", "id": "categoria"},
        {"name": "Pasillo", "id": "pasillo"}, {"name": "Rack", "id": "rack"},
        {"name": "Marca", "id": "marca"}, {"name": "Stock", "id": "stock", "type": "numeric", "format": NUM_FMT},
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
    Input("f-maneja_stock", "value"),
)
def _selection_panel(seleccion, tienda, modo, mes, semana, familia, categoria, clasificacion,
                     zona_pck, responsable, marca, maneja):
    if not tienda:
        return None
    anio = db.get_anio_actual(tienda)
    filtros = make_filters(maneja, familia, categoria, clasificacion, zona_pck, responsable, marca)
    mes_sel = mes if modo == "Mes" else None
    semana_sel = semana if modo == "Semana" else None
    if not seleccion:
        acciones = db.get_acciones_rack(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros)
        accionables = acciones[acciones["accion"] != "Mantener"] if not acciones.empty else acciones
        if len(accionables):
            top = accionables.iloc[0]
            return html.Div([
                html.Div("Sin sección seleccionada", className="diagnostic-kicker"),
                html.H3("Empieza por la primera acción", className="diagnostic-title"),
                html.P(f"Rack {top['rack']}: {top['accion']}. {top['motivo']}", className="diagnostic-text"),
                html.Div("Puedes hacer clic en ese rack en la tabla de acciones o directamente sobre el plano.", className="diagnostic-hint"),
            ])
        return html.Div([
            html.Div("Sin sección seleccionada", className="diagnostic-kicker"),
            html.H3("Explora un rack", className="diagnostic-title"),
            html.P("Haz clic en el mapa para ver KPIs, tendencia y una recomendación explicada.", className="diagnostic-text"),
        ])

    pasillo_f = seleccion["clave"] if seleccion.get("nivel") == "pasillo" else None
    rack_f = seleccion["clave"] if seleccion.get("nivel") == "rack" else None
    resumen = db.get_resumen_periodo(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                     pasillo=pasillo_f, rack=rack_f)

    title = f"Rack {rack_f}" if rack_f else f"Pasillo {pasillo_f}"
    metrics = html.Div([
        html.Div([html.Span("Venta"), html.Strong(fmt_money_short(resumen.get("venta")))]),
        html.Div([html.Span("Variación"), html.Strong(fmt_pct(resumen.get("variacion_pct")))]),
        html.Div([html.Span("SKUs"), html.Strong(f"{int(resumen.get('skus') or 0):,}".replace(",", "."))]),
        html.Div([html.Span("Unidades"), html.Strong(f"{float(resumen.get('unidades') or 0):,.0f}".replace(",", "."))]),
    ], className="diagnostic-metrics")

    acciones = db.get_acciones_rack(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros)
    if rack_f and not acciones.empty:
        match = acciones[acciones["rack"] == rack_f]
        if len(match):
            r = match.iloc[0]
            action_box = html.Div([
                html.Div([html.Span(r["prioridad"], className=f"priority-pill priority-{r['prioridad'].lower()}"),
                          html.Span("Recomendación del período", className="diagnostic-kicker")], className="action-hero-top"),
                html.H4(r["accion"], className="diagnostic-action"),
                html.P(r["motivo"], className="diagnostic-text"),
                html.P(r["recomendacion"], className="diagnostic-recommendation"),
            ], className="diagnostic-action-box")
        else:
            action_box = html.Div("No hay una recomendación específica para este rack con los filtros actuales.", className="empty-note")
    elif pasillo_f and not acciones.empty:
        sub = acciones[(acciones["pasillo"] == pasillo_f) & (acciones["accion"] != "Mantener")]
        counts = sub["accion"].value_counts() if len(sub) else pd.Series(dtype=int)
        action_box = html.Div([
            html.Div("Acciones dentro del pasillo", className="diagnostic-kicker"),
            html.P(", ".join(f"{k}: {v}" for k, v in counts.items()) if len(counts) else "Sin acciones urgentes en este pasillo.",
                   className="diagnostic-text"),
        ], className="diagnostic-action-box")
    else:
        action_box = None

    categorias = db.get_categorias_seccion(
        tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
        pasillo=pasillo_f, rack=rack_f, n=6,
    )
    if categorias is not None and not categorias.empty:
        cat_rows = []
        for r in categorias.itertuples():
            cat_rows.append(html.Div([
                html.Div([
                    html.Strong(str(r.categoria), className="category-name"),
                    html.Span(str(r.familia), className="category-family"),
                ]),
                html.Div([
                    html.Strong(fmt_money_short(r.venta)),
                    html.Span(f"{float(r.participacion_pct):.1f}% venta · {int(r.skus_con_venta)}/{int(r.skus_asociados)} SKU con venta"),
                ], className="category-values"),
            ], className="category-row"))
        category_box = html.Div([
            html.Div("Categorías asociadas a esta sección", className="diagnostic-kicker"),
            html.Div("Incluye el surtido vigente; la fracción indica SKU con venta / SKU asociados.", className="diagnostic-hint"),
            *cat_rows,
        ], className="diagnostic-category-box")
    else:
        category_box = None

    return html.Div([
        html.Div("Sección seleccionada", className="diagnostic-kicker"),
        html.H3(title, className="diagnostic-title"),
        metrics,
        action_box,
        category_box,
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
    prevent_initial_call=True,
)
def _descargar_detalle(_, tienda, modo, mes, semana, familia, categoria, clasificacion,
                       zona_pck, responsable, marca, maneja, seleccion):
    if not tienda:
        return dash.no_update
    anio = db.get_anio_actual(tienda)
    mes_sel = mes if modo == "Mes" else None
    semana_sel = semana if modo == "Semana" else None
    filtros = make_filters(maneja, familia, categoria, clasificacion, zona_pck, responsable, marca)
    pasillo_f = seleccion["clave"] if seleccion and seleccion.get("nivel") == "pasillo" else None
    rack_f = seleccion["clave"] if seleccion and seleccion.get("nivel") == "rack" else None
    df = db.get_detalle_productos(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                  pasillo=pasillo_f, rack=rack_f)
    if df.empty:
        return dash.no_update
    sufijo = rack_f or pasillo_f or "tienda"
    return dcc.send_data_frame(df.to_csv, f"detalle_racks_{tienda}_{sufijo}_{anio}.csv",
                               index=False, sep=";", encoding="utf-8-sig")


@app.callback(
    Output("download-sinventa", "data"),
    Input("btn-download-sinventa", "n_clicks"),
    State("f-tienda", "value"), State("f-familia", "value"), State("f-categoria", "value"),
    State("f-clasificacion", "value"), State("f-zona_pck", "value"),
    State("f-responsable_linea", "value"), State("f-marca", "value"), State("f-maneja_stock", "value"),
    State("store-seleccion", "data"),
    prevent_initial_call=True,
)
def _descargar_sinventa(_, tienda, familia, categoria, clasificacion, zona_pck,
                        responsable, marca, maneja, seleccion):
    if not tienda:
        return dash.no_update
    anio = db.get_anio_actual(tienda)
    filtros = make_filters(maneja, familia, categoria, clasificacion, zona_pck, responsable, marca)
    pasillo_f = seleccion["clave"] if seleccion and seleccion.get("nivel") == "pasillo" else None
    rack_f = seleccion["clave"] if seleccion and seleccion.get("nivel") == "rack" else None
    df = db.get_acciones_producto(tienda, anio, filtros=filtros, n=None, pasillo=pasillo_f, rack=rack_f)
    if df.empty:
        return dash.no_update
    sufijo = rack_f or pasillo_f or "tienda"
    return dcc.send_data_frame(df.to_csv, f"stock_sin_venta_{tienda}_{sufijo}_{anio}.csv",
                               index=False, sep=";", encoding="utf-8-sig")


# =========================
# Figuras
# =========================
def _figura_mapa(tienda, anio, mes_sel, semana_sel, filtros, nivel_mapa, seleccion):
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

    venta_nivel = db.get_venta_por_nivel(tienda, anio, mes=mes_sel, semana=semana_sel,
                                         filtros=filtros, nivel=nivel_mapa)
    venta_map = venta_nivel.set_index("clave")["venta"].to_dict() if len(venta_nivel) else {}
    coords = coords.copy()
    coords["venta"] = pd.to_numeric(coords["clave"].map(venta_map), errors="coerce").fillna(0.0)
    max_v = max(float(coords["venta"].max()), 1.0)
    positivos = coords.loc[coords["venta"] > 0, "venta"]
    tope_color = max(float(positivos.quantile(0.90)), 1.0) if len(positivos) else 1.0

    img_b64 = base64.b64encode(plano["imagen"]).decode("ascii")
    fig.add_layout_image(dict(
        source=f"data:image/png;base64,{img_b64}", xref="x", yref="y", x=0, y=0,
        sizex=plano["img_w"], sizey=plano["img_h"], sizing="stretch", layer="below",
    ))
    sizes = 9 + 23 * (coords["venta"] / max_v).pow(0.5)
    fig.add_trace(go.Scatter(
        x=coords["x"], y=coords["y"], mode="markers", customdata=coords["clave"],
        marker=dict(size=sizes, color=coords["venta"], colorscale="Blues", cmin=0, cmax=tope_color,
                    showscale=True, colorbar=dict(title="Venta", tickprefix="$", thickness=12, len=.55),
                    line=dict(width=1.2, color="white"), opacity=.93),
        text=[f"{nivel_mapa.capitalize()} {c}<br><b>{fmt_money(v)}</b>" for c, v in zip(coords["clave"], coords["venta"])],
        hoverinfo="text",
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
    familias = tree.groupby("familia", as_index=False)["venta"].sum()
    jefes = tree.groupby(["familia", "jefe_linea"], as_index=False)["venta"].sum()
    ids = (list(familias["familia"]) +
           [f"{r.familia}|{r.jefe_linea}" for r in jefes.itertuples()] +
           [f"{r.familia}|{r.jefe_linea}|{r.categoria}" for r in tree.itertuples()])
    labels = list(familias["familia"]) + list(jefes["jefe_linea"]) + list(tree["categoria"])
    parents = ([""] * len(familias) + list(jefes["familia"]) +
               [f"{r.familia}|{r.jefe_linea}" for r in tree.itertuples()])
    values = list(familias["venta"]) + list(jefes["venta"]) + list(tree["venta"])
    fig.add_trace(go.Treemap(
        ids=ids, labels=labels, parents=parents, values=values, branchvalues="total",
        marker=dict(colors=values, colorscale="Blues", line=dict(width=1, color="white")),
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
@app.callback(Output("tabla-combos", "data"), Output("tabla-combos", "columns"),
              Input("f-tienda", "value"), Input("combo-orden", "value"), Input("store-seleccion", "data"))
def _combos_top(tienda, orden, seleccion):
    if not tienda:
        return [], []
    pasillo_f = seleccion["clave"] if seleccion and seleccion.get("nivel") == "pasillo" else None
    rack_f = seleccion["clave"] if seleccion and seleccion.get("nivel") == "rack" else None
    df = db.get_top_combos(tienda, n=40, orden=orden, pasillo=pasillo_f, rack=rack_f)
    cols = [
        {"name": "Producto A", "id": "desc_a"}, {"name": "Producto B", "id": "desc_b"},
        {"name": "Boletas juntas", "id": "boletas", "type": "numeric", "format": NUM_FMT},
        {"name": "Soporte", "id": "soporte", "type": "numeric"},
        {"name": "Confianza", "id": "confianza_a_b", "type": "numeric"},
        {"name": "Lift", "id": "lift", "type": "numeric"},
    ]
    return clean_records(df), cols


@app.callback(Output("combo-producto", "options"), Input("f-tienda", "value"))
def _combo_producto_opts(tienda):
    if not tienda:
        return []
    df = db.get_productos_lista(tienda)
    return [{"label": r.descripcion, "value": r.sku} for r in df.itertuples()]


@app.callback(Output("tabla-combos-producto", "data"), Output("tabla-combos-producto", "columns"),
              Input("f-tienda", "value"), Input("combo-producto", "value"))
def _combos_producto(tienda, sku):
    if not tienda or not sku:
        return [], []
    df = db.get_combos_de_producto(tienda, sku, n=15)
    cols = [
        {"name": "Se compra junto con", "id": "producto"},
        {"name": "Boletas juntas", "id": "boletas", "type": "numeric", "format": NUM_FMT},
        {"name": "Confianza", "id": "confianza", "type": "numeric"},
        {"name": "Lift", "id": "lift", "type": "numeric"},
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
