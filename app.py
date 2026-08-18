import base64
import os

import dash
from dash import dcc, html, Input, Output, State, dash_table, callback_context
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import pandas as pd

import db
from auth import check_password

FMT = lambda n: f"${n:,.0f}".replace(",", ".") if pd.notna(n) else "—"
FILTER_KEYS = ["familia", "categoria", "clasificacion", "zona_pck", "responsable_linea", "marca", "maneja_stock"]

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.FLATLY,
                           "https://fonts.googleapis.com/css2?family=Archivo+Expanded:wght@700;800"
                           "&family=Barlow:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap"],
    title="Rentabilidad Rack — Imperial",
    suppress_callback_exceptions=True,
)
server = app.server

TABLE_STYLE = dict(
    style_as_list_view=True,
    style_header={"backgroundColor": "#F7F8F7", "fontWeight": "700", "fontSize": "11px",
                   "textTransform": "uppercase", "letterSpacing": ".04em", "color": "#5B6B79",
                   "border": "none", "borderBottom": "1px solid #DCE1E0"},
    style_cell={"fontFamily": "Barlow, sans-serif", "fontSize": "13px", "padding": "7px 10px",
                "border": "none", "borderBottom": "1px solid #EEF0EF", "textAlign": "left"},
    style_data={"backgroundColor": "white"},
    page_size=12,
    sort_action="native",
    filter_action="native",
    export_format="csv",
)


def kpi_card(id_prefix):
    return dbc.Card(dbc.CardBody([
        html.Div(id=f"{id_prefix}-value", className="kpi-value"),
        html.Div(id=f"{id_prefix}-label", className="kpi-label"),
    ]), className="kpi-card")


def section(title, children, subtitle=None):
    head = [html.H2(title, className="section-title")]
    if subtitle:
        head.append(html.Div(subtitle, className="section-subtitle"))
    return html.Div([*head, *children], className="section-panel")


# ---------------- Layout ----------------
sidebar = html.Div([
    html.Div([
        html.Div("🔧 RENTABILIDAD RACK", className="brand-mark"),
        html.Div("Imperial Ferretería · venta por sección", className="brand-sub"),
    ], className="brand"),

    html.Div("Tienda", className="side-label"),
    dcc.Dropdown(id="f-tienda", clearable=False, className="side-dd"),

    html.Hr(className="side-hr"),
    html.Div("Ver por", className="side-label"),
    dcc.RadioItems(id="f-modo-periodo", options=["Mes", "Semana", "Año completo"], value="Mes",
                    className="side-radio", labelClassName="side-radio-item"),
    dcc.Dropdown(id="f-mes", clearable=False, className="side-dd"),
    dcc.Dropdown(id="f-semana", clearable=False, className="side-dd", style={"display": "none"}),

    html.Hr(className="side-hr"),
    html.Div("Filtros de producto", className="side-label side-label-strong"),
    dcc.Dropdown(id="f-familia", multi=True, placeholder="Familia", className="side-dd"),
    dcc.Dropdown(id="f-categoria", multi=True, placeholder="Categoría", className="side-dd"),
    dcc.Dropdown(id="f-clasificacion", multi=True, placeholder="Clasificación SKU", className="side-dd"),
    dcc.Dropdown(id="f-zona_pck", multi=True, placeholder="Zona de picking", className="side-dd"),
    dcc.Dropdown(id="f-responsable_linea", multi=True, placeholder="Jefe de línea", className="side-dd"),
    dcc.Dropdown(id="f-marca", multi=True, placeholder="Marca", className="side-dd"),
    dcc.RadioItems(id="f-maneja_stock", options=["Todos", "Sí", "No"], value="Todos",
                    className="side-radio", labelClassName="side-radio-item"),

    html.Hr(className="side-hr"),
    html.Hr(className="side-hr"),
    dcc.Link("🗺️ Administrar planos", href="/admin", className="side-navlink"),

    html.Div(id="seleccion-mapa-info", className="side-selection"),

    html.Hr(className="side-hr"),
    html.Div("Datos sincronizados por el agente de escritorio — no en vivo minuto a minuto.",
             className="side-foot"),
], className="sidebar")

main = html.Div([
    html.Div(id="header-tienda"),
    html.Div(id="kpi-row"),
    html.Div(id="margen-warning"),

    section("Venta sobre el plano", [
        dbc.RadioItems(id="f-nivel-mapa", options=["Pasillo", "Rack"], value="Pasillo",
                        inline=True, className="nivel-toggle"),
        html.Div("Haz clic en un punto para filtrar todo el resto de la página por esa sección "
                  "— clic de nuevo (o \"Quitar selección\") para volver a ver todo.",
                  className="section-subtitle"),
        dcc.Graph(id="mapa-calor", config={"displayModeBar": False}),
    ]),

    dbc.Row([
        dbc.Col(section("Venta por pasillo", [dash_table.DataTable(id="tabla-pasillos", **TABLE_STYLE)]), md=6),
        dbc.Col(section("Detalle por rack", [dash_table.DataTable(id="tabla-racks", **TABLE_STYLE)]), md=6),
    ], className="g-3"),

    section("Recomendación de espacio por pasillo/rack", [
        html.Div(id="recom-resumen"),
        dash_table.DataTable(id="tabla-recomendacion", **TABLE_STYLE),
    ], subtitle="Compara año a la fecha contra el mismo rango de semanas del año anterior."),

    section("Venta por familia → jefe de línea → categoría", [
        dcc.Graph(id="treemap-familia", config={"displayModeBar": False}),
    ], subtitle="Bloques navegables — haz clic para entrar a cada nivel."),

    html.Div(id="sin-coordenadas-panel"),

    dbc.Row([
        dbc.Col(section("Top productos", [
            dash_table.DataTable(id="tabla-top", **TABLE_STYLE)]), md=6),
        dbc.Col(section("Menor venta (con transacciones)", [
            dash_table.DataTable(id="tabla-baja", **TABLE_STYLE)]), md=6),
    ], className="g-3"),

    section("Con stock, sin venta este período", [
        html.Span("candidatos a cambiar", className="badge-bad"),
        dash_table.DataTable(id="tabla-sinventa", **TABLE_STYLE),
    ]),

    section("Comparativo año a la fecha vs año anterior", [
        html.Div(id="comparativo-cards"),
    ]),

    section("Cross-sell y combos", [
        html.Div("Calculado sobre las boletas del año — pares de productos con más soporte/confianza/lift.",
                  className="section-subtitle"),
        dbc.Row([
            dbc.Col([
                html.Div("Top combinaciones de la tienda", className="subblock-title"),
                dcc.Dropdown(id="combo-orden",
                             options=[{"label": "Más frecuentes", "value": "boletas"},
                                      {"label": "Mayor lift", "value": "lift"},
                                      {"label": "Mayor confianza", "value": "confianza"}],
                             value="boletas", clearable=False, className="side-dd"),
                dash_table.DataTable(id="tabla-combos", **TABLE_STYLE),
            ], md=6),
            dbc.Col([
                html.Div("Buscar combinaciones de un producto", className="subblock-title"),
                dcc.Dropdown(id="combo-producto", placeholder="Elige un producto…"),
                dash_table.DataTable(id="tabla-combos-producto", **TABLE_STYLE),
            ], md=6),
        ], className="g-3"),
    ]),

    dcc.Store(id="store-seleccion", data=None),
], className="main")

app.layout = html.Div([
    dcc.Location(id="url"),
    dcc.Store(id="store-auth", storage_type="session"),
    html.Div(id="page-content"),
])

login_layout = html.Div([
    html.Div([
        html.Div("🔧 RENTABILIDAD RACK", className="brand-mark", style={"color": "#1B2430"}),
        html.P("Imperial Ferretería", style={"color": "#5B6B79", "marginBottom": "18px"}),
        dcc.Input(id="login-pwd", type="password", placeholder="Contraseña", className="login-input"),
        html.Button("Entrar", id="login-btn", className="login-btn"),
        html.Div(id="login-error", style={"color": "#C4432B", "marginTop": "10px", "fontSize": "13px"}),
    ], className="login-box")
], className="login-wrap")

dashboard_layout = html.Div([sidebar, main], className="shell")

admin_sidebar = html.Div([
    html.Div([
        html.Div("🗺️ ADMINISTRAR PLANOS", className="brand-mark"),
        html.Div("Sube el plano y coordenadas de cada tienda", className="brand-sub"),
    ], className="brand"),
    dcc.Link("← Volver al dashboard", href="/", className="side-navlink"),
    html.Hr(className="side-hr"),
    html.Div("Tienda", className="side-label"),
    dcc.Dropdown(id="admin-tienda", clearable=False, className="side-dd"),
], className="sidebar")

admin_main = html.Div([
    html.H1("Administrar planos"),
    section("1. Imagen del plano", [
        dcc.Upload(id="admin-upload-imagen", children=html.Div(["Arrastra o ", html.A("elige un archivo")]),
                    className="upload-box", accept="image/png,image/jpeg"),
        html.Div(id="admin-imagen-preview"),
        html.Button("Guardar plano", id="admin-guardar-plano", className="login-btn", style={"width": "200px", "marginTop": "10px"}),
        html.Div(id="admin-plano-msg"),
    ]),
    section("2. Coordenadas", [
        dbc.RadioItems(id="admin-nivel", options=["Pasillo", "Rack"], value="Pasillo", inline=True),
        html.Div("Sube un CSV con columnas pasillo|rack, x, y (posición en píxeles sobre la imagen; "
                  "esquina superior izquierda = 0,0).", className="section-subtitle"),
        dcc.Upload(id="admin-upload-csv", children=html.Div(["Arrastra o ", html.A("elige un CSV")]),
                    className="upload-box", accept=".csv"),
        html.Button("Guardar coordenadas", id="admin-guardar-coords", className="login-btn", style={"width": "220px", "marginTop": "10px"}),
        html.Div(id="admin-coords-msg"),
    ]),
    section("Coordenadas actuales", [
        dbc.Row([
            dbc.Col([html.Div("Por pasillo", className="subblock-title"),
                     dash_table.DataTable(id="admin-tabla-pasillo", **TABLE_STYLE)], md=6),
            dbc.Col([html.Div("Por rack", className="subblock-title"),
                     dash_table.DataTable(id="admin-tabla-rack", **TABLE_STYLE)], md=6),
        ]),
    ]),
    dcc.Store(id="admin-imagen-store"),
], className="main")

admin_layout = html.Div([admin_sidebar, admin_main], className="shell")


# ================= Routing / login =================
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


# ================= Inicialización de datos (una vez al levantar el server) =================
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
    opts = [{"label": f"{r.cod_tienda} — {r.tipo}", "value": r.cod_tienda} for r in tiendas.itertuples()]
    return opts, (tiendas["cod_tienda"].iloc[0] if len(tiendas) else None)


# ================= Sidebar: período =================
@app.callback(
    Output("f-mes", "options"), Output("f-mes", "value"), Output("f-mes", "style"),
    Output("f-semana", "options"), Output("f-semana", "value"), Output("f-semana", "style"),
    Input("f-tienda", "value"), Input("f-modo-periodo", "value"),
)
def _periodo_opts(tienda, modo):
    if not tienda:
        return [], None, {"display": "none"}, [], None, {"display": "none"}
    meses, semanas = db.get_periodos(tienda)
    anio = int(meses["anio"].max())
    meses_disp = meses[meses["anio"] == anio]["mes"].tolist()
    semanas_disp = semanas[semanas["anio"] == anio]["semana"].tolist()
    mes_opts = [{"label": f"Mes {m}", "value": m} for m in meses_disp]
    sem_opts = [{"label": f"Semana {s}", "value": s} for s in semanas_disp]
    mostrar_mes = {"display": "block"} if modo == "Mes" else {"display": "none"}
    mostrar_sem = {"display": "block"} if modo == "Semana" else {"display": "none"}
    return mes_opts, (meses_disp[-1] if meses_disp else None), mostrar_mes, \
        sem_opts, (semanas_disp[-1] if semanas_disp else None), mostrar_sem


# ================= Sidebar: opciones de filtro =================
@app.callback(
    Output("f-familia", "options"), Output("f-categoria", "options"), Output("f-clasificacion", "options"),
    Output("f-zona_pck", "options"), Output("f-responsable_linea", "options"), Output("f-marca", "options"),
    Input("f-tienda", "value"),
)
def _filtro_opts(tienda):
    if not tienda:
        return [], [], [], [], [], []
    op = db.get_opciones_filtro(tienda)
    return (op["familia"], op["categoria"], op["clasificacion"], op["zona_pck"],
            op["responsable_linea"], op["marca"])


# ================= Selección en el mapa (clic) =================
@app.callback(
    Output("store-seleccion", "data"),
    Input("mapa-calor", "clickData"), Input("f-nivel-mapa", "value"),
    Input("f-tienda", "value"), Input("f-mes", "value"), Input("f-semana", "value"),
    State("store-seleccion", "data"),
    prevent_initial_call=True,
)
def _click_mapa(clickData, nivel, tienda, mes, semana, actual):
    trig = callback_context.triggered_id
    if trig in ("f-tienda", "f-mes", "f-semana", "f-nivel-mapa"):
        return None  # cambiar tienda/periodo/nivel limpia la selección
    if clickData:
        clave = clickData["points"][0].get("customdata")
        if clave:
            nivel_key = "pasillo" if nivel == "Pasillo" else "rack"
            if actual and actual.get("nivel") == nivel_key and actual.get("clave") == clave:
                return None  # clic de nuevo sobre el mismo punto = deseleccionar
            return {"nivel": nivel_key, "clave": clave}
    return actual


@app.callback(Output("seleccion-mapa-info", "children"), Input("store-seleccion", "data"))
def _seleccion_info(sel):
    if not sel:
        return html.Div("Sin sección filtrada desde el mapa.", className="side-foot")
    return html.Div([
        html.Div(f"Filtrando por {sel['nivel']}: {sel['clave']}", className="side-selection-active"),
    ])


# ================= Callback principal: arma todos los datos del dashboard =================
@app.callback(
    Output("header-tienda", "children"),
    Output("kpi-row", "children"),
    Output("margen-warning", "children"),
    Output("mapa-calor", "figure"),
    Output("tabla-pasillos", "data"), Output("tabla-pasillos", "columns"),
    Output("tabla-racks", "data"), Output("tabla-racks", "columns"),
    Output("recom-resumen", "children"),
    Output("tabla-recomendacion", "data"), Output("tabla-recomendacion", "columns"),
    Output("treemap-familia", "figure"),
    Output("sin-coordenadas-panel", "children"),
    Output("tabla-top", "data"), Output("tabla-top", "columns"),
    Output("tabla-baja", "data"), Output("tabla-baja", "columns"),
    Output("tabla-sinventa", "data"), Output("tabla-sinventa", "columns"),
    Output("comparativo-cards", "children"),
    Input("f-tienda", "value"), Input("f-modo-periodo", "value"),
    Input("f-mes", "value"), Input("f-semana", "value"),
    Input("f-familia", "value"), Input("f-categoria", "value"), Input("f-clasificacion", "value"),
    Input("f-zona_pck", "value"), Input("f-responsable_linea", "value"), Input("f-marca", "value"),
    Input("f-maneja_stock", "value"), Input("f-nivel-mapa", "value"),
    Input("store-seleccion", "data"),
)
def _actualizar(tienda, modo, mes, semana, familia, categoria, clasificacion, zona_pck,
                 responsable_linea, marca, maneja_sel, nivel_mapa_lbl, seleccion):
    if not tienda:
        return [dash.no_update] * 20

    mes_sel = mes if modo == "Mes" else None
    semana_sel = semana if modo == "Semana" else None
    filtros = {
        "familia": familia or [], "categoria": categoria or [], "clasificacion": clasificacion or [],
        "zona_pck": zona_pck or [], "responsable_linea": responsable_linea or [], "marca": marca or [],
        "maneja_stock": ["S"] if maneja_sel == "Sí" else ["N"] if maneja_sel == "No" else [],
    }
    nivel_mapa = "pasillo" if nivel_mapa_lbl == "Pasillo" else "rack"

    tiendas = db.get_tiendas()
    tipo_tienda = tiendas.set_index("cod_tienda").loc[tienda, "tipo"]
    anio = 2026  # dataset actual -- si se agregan años futuros, calcular como max(meses.anio)

    pasillos = db.get_pasillo_resumen(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros)
    racks = db.get_rack_detalle(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros)

    pasillo_f = seleccion["clave"] if seleccion and seleccion["nivel"] == "pasillo" else None
    rack_f = seleccion["clave"] if seleccion and seleccion["nivel"] == "rack" else None
    # si se filtró por rack, también acotamos el pasillo al que pertenece para las tablas de producto
    pasillo_para_prod = pasillo_f
    if rack_f and not racks.empty:
        m = racks[racks["rack"] == rack_f]
        if len(m):
            pasillo_para_prod = None  # el rack ya identifica la seccion, no hace falta acotar pasillo tambien

    top = db.get_top_productos(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                pasillo=pasillo_f, rack=rack_f, n=50)
    baja = db.get_top_productos(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                 pasillo=pasillo_f, rack=rack_f, n=50, ascendente=True)
    sin_venta = db.get_sin_venta(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros, n=300)

    periodo_txt = f"Semana {semana_sel}" if semana_sel else (f"Mes {mes_sel}/{anio}" if mes_sel else f"Año {anio}")
    n_filtros = sum(len(v) for v in filtros.values())

    header = html.Div([
        html.H1(tienda),
        html.Div([
            html.Span(tipo_tienda, className="badge-good"),
            html.Span(periodo_txt, className="header-periodo"),
            html.Span(f"{n_filtros} filtro(s) activo(s)", className="header-periodo") if n_filtros else None,
        ]),
    ])

    kpis = dbc.Row([
        dbc.Col(_kpi(FMT(pasillos["venta"].sum()) if len(pasillos) else "$0", "Venta del período"), md=3),
        dbc.Col(_kpi(str(int(pasillos["pasillo"].nunique())) if len(pasillos) else "0", "Pasillos con venta"), md=3),
        dbc.Col(_kpi(str(int(racks["rack"].nunique())) if len(racks) else "0", "Racks con venta"), md=3),
        dbc.Col(_kpi(str(len(sin_venta)), "SKU con stock sin venta"), md=3),
    ], className="g-3")

    margen_warn = None
    if len(pasillos) and pasillos["margen"].sum() == 0:
        margen_warn = dbc.Alert(
            "⚠️ Margen en $0 — bug conocido en el JOIN de costos del origen de datos. La venta sí es real.",
            color="warning", className="margen-alert")

    fig_mapa = _figura_mapa(tienda, anio, mes_sel, semana_sel, filtros, nivel_mapa)

    cols_pasillo = [{"name": n, "id": c} for n, c in
                     [("Pasillo", "pasillo"), ("Racks", "racks"), ("Venta", "venta"), ("Margen", "margen")]]
    cols_rack = [{"name": n, "id": c} for n, c in
                 [("Pasillo", "pasillo"), ("Rack", "rack"), ("Venta", "venta"), ("Margen", "margen")]]

    recom = db.get_recomendacion_pasillo(tienda)
    if recom.empty:
        recom_resumen = html.Div("Sin datos suficientes para comparar años todavía.", className="section-subtitle")
        recom_data, recom_cols = [], []
    else:
        counts = recom["recomendacion"].value_counts()
        badge_class = {"Aumentar espacio": "badge-good", "Mantener": "badge-neutral",
                        "Revisar": "badge-neutral", "Reducir espacio": "badge-bad"}
        recom_resumen = html.Div([
            html.Span(f"{k}: {v}", className=badge_class.get(k, "badge-neutral")) for k, v in counts.items()
        ], className="recom-badges")
        recom_data = recom.round(1).to_dict("records")
        recom_cols = [{"name": n, "id": c} for n, c in
                      [("Pasillo", "pasillo"), ("Rack", "rack"), ("Venta", "venta"),
                       ("Venta año anterior", "venta_anio_anterior"), ("Variación %", "variacion_pct"),
                       ("Recomendación", "recomendacion")]]

    tree = db.get_treemap(tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros)
    fig_tree = _figura_treemap(tree)

    sin_coord = db.get_sin_coordenadas(tienda, nivel=nivel_mapa)
    sin_coord_panel = None
    if not sin_coord.empty:
        sin_coord_panel = section(
            f"{nivel_mapa.capitalize()}s con venta pero sin coordenada en el plano",
            [dash_table.DataTable(
                data=sin_coord.round(0).to_dict("records"),
                columns=[{"name": nivel_mapa.capitalize(), "id": "clave"}, {"name": "Venta", "id": "venta"}],
                **TABLE_STYLE)],
            subtitle="No aparecen en el mapa de calor porque el plano no tiene su posición cargada todavía.")

    def _cols_prod(extra=()):
        base = [("SKU", "cod_rapido"), ("Producto", "descripcion"), ("Marca", "marca"),
                ("Maneja stock", "maneja_stock"), ("Stock", "stock")]
        return [{"name": n, "id": c} for n, c in base + list(extra)]

    top_cols = _cols_prod([("Venta", "venta"), ("Cantidad", "cantidad")])
    baja_cols = _cols_prod([("Venta", "venta"), ("Cantidad", "cantidad")])
    sinventa_cols = [{"name": n, "id": c} for n, c in
                      [("SKU", "cod_rapido"), ("Producto", "descripcion"), ("Marca", "marca"), ("Stock", "stock")]]

    comp = db.get_comparativo_anio(tienda)
    comp_cards = _comparativo_cards(comp)

    return (
        header, kpis, margen_warn, fig_mapa,
        pasillos.round(0).to_dict("records"), cols_pasillo,
        racks.round(0).to_dict("records"), cols_rack,
        recom_resumen, recom_data, recom_cols,
        fig_tree, sin_coord_panel,
        top.round(0).to_dict("records"), top_cols,
        baja.round(0).to_dict("records"), baja_cols,
        sin_venta.round(0).to_dict("records"), sinventa_cols,
        comp_cards,
    )


def _kpi(value, label):
    return dbc.Card(dbc.CardBody([html.Div(value, className="kpi-value"),
                                   html.Div(label, className="kpi-label")]), className="kpi-card")


def _figura_mapa(tienda, anio, mes_sel, semana_sel, filtros, nivel_mapa):
    plano = db.get_plano(tienda)
    coords = db.get_coords(tienda, nivel=nivel_mapa)
    fig = go.Figure()
    if plano is None or coords.empty:
        fig.add_annotation(text=f"Todavía no hay coordenadas de {nivel_mapa} para {tienda}. "
                                 "Súbelas en la página Administrar Planos.",
                            showarrow=False, font=dict(size=14, color="#5B6B79"))
        fig.update_xaxes(visible=False)
        fig.update_yaxes(visible=False)
        fig.update_layout(height=500, plot_bgcolor="#F7F8F7")
        return fig

    venta_nivel = db.get_venta_por_nivel(tienda, anio, mes=mes_sel, semana=semana_sel,
                                          filtros=filtros, nivel=nivel_mapa)
    venta_map = venta_nivel.set_index("clave")["venta"].to_dict()
    coords = coords.copy()
    coords["venta"] = coords["clave"].map(venta_map).fillna(0)
    max_v = max(coords["venta"].max(), 1)
    positivos = coords.loc[coords["venta"] > 0, "venta"]
    tope_color = max(positivos.quantile(0.90), 1) if len(positivos) else 1

    img_b64 = base64.b64encode(plano["imagen"]).decode("ascii")
    fig.add_layout_image(dict(source=f"data:image/png;base64,{img_b64}", xref="x", yref="y",
                               x=0, y=0, sizex=plano["img_w"], sizey=plano["img_h"],
                               sizing="stretch", layer="below"))
    fig.add_trace(go.Scatter(
        x=coords["x"], y=coords["y"], mode="markers", customdata=coords["clave"],
        marker=dict(size=8 + 30 * (coords["venta"] / max_v) ** 0.5, color=coords["venta"],
                    colorscale="Turbo", cmin=0, cmax=tope_color, showscale=True,
                    colorbar=dict(title="Venta", tickprefix="$"), line=dict(width=1, color="white")),
        text=[f"{nivel_mapa.capitalize()} {c}<br>{FMT(v)}" for c, v in zip(coords["clave"], coords["venta"])],
        hoverinfo="text",
    ))
    fig.update_xaxes(visible=False, range=[0, plano["img_w"]])
    fig.update_yaxes(visible=False, range=[plano["img_h"], 0])
    fig.update_layout(height=550, margin=dict(l=0, r=0, t=10, b=0), plot_bgcolor="white",
                       clickmode="event+select")
    return fig


def _figura_treemap(tree):
    fig = go.Figure()
    if tree.empty:
        fig.update_layout(height=480, plot_bgcolor="#F7F8F7")
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
    fig.add_trace(go.Treemap(ids=ids, labels=labels, parents=parents, values=values, branchvalues="total",
                              marker=dict(colors=values, colorscale="Turbo", line=dict(width=1, color="white")),
                              texttemplate="%{label}<br>$%{value:,.0f}"))
    fig.update_layout(height=480, margin=dict(l=0, r=0, t=10, b=0))
    return fig


def _comparativo_cards(comp):
    if comp is None or len(comp) < 2:
        return html.Div("Sin datos suficientes para comparar años.", className="section-subtitle")
    a_actual, a_ant = comp.iloc[-1], comp.iloc[-2]

    def _var(actual, anterior):
        if not anterior:
            return None
        return (actual - anterior) / anterior * 100

    filas = [
        ("Venta", FMT(a_actual.venta), FMT(a_ant.venta), _var(a_actual.venta, a_ant.venta)),
        ("Transacciones", f"{int(a_actual.trx):,}".replace(",", "."), f"{int(a_ant.trx):,}".replace(",", "."),
         _var(a_actual.trx, a_ant.trx)),
        ("Ticket promedio", FMT(a_actual.ticket_promedio), FMT(a_ant.ticket_promedio),
         _var(a_actual.ticket_promedio, a_ant.ticket_promedio)),
        ("Clientes únicos", f"{int(a_actual.clientes):,}".replace(",", "."),
         f"{int(a_ant.clientes):,}".replace(",", "."), _var(a_actual.clientes, a_ant.clientes)),
    ]
    cards = []
    for nombre, val, val_ant, var in filas:
        color = "#1E8A5B" if (var or 0) >= 0 else "#C4432B"
        flecha = "▲" if (var or 0) >= 0 else "▼"
        cards.append(dbc.Col(dbc.Card(dbc.CardBody([
            html.Div(nombre, className="kpi-label"),
            html.Div(val, className="kpi-value"),
            html.Div(f"{flecha} {abs(var):.1f}%  ·  {val_ant} año anterior" if var is not None else "—",
                      style={"color": color, "fontSize": "12px", "marginTop": "4px"}),
        ]), className="kpi-card"), md=3))
    return dbc.Row(cards, className="g-3")


# ================= Cross-sell =================
@app.callback(Output("tabla-combos", "data"), Output("tabla-combos", "columns"),
              Input("f-tienda", "value"), Input("combo-orden", "value"))
def _combos_top(tienda, orden):
    if not tienda:
        return [], []
    df = db.get_top_combos(tienda, n=40, orden=orden)
    cols = [{"name": n, "id": c} for n, c in
            [("Producto A", "desc_a"), ("Producto B", "desc_b"), ("Boletas juntas", "boletas"),
             ("Soporte", "soporte"), ("Confianza", "confianza_a_b"), ("Lift", "lift")]]
    return df.to_dict("records"), cols


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
    cols = [{"name": n, "id": c} for n, c in
            [("Se compra junto con", "producto"), ("Boletas juntas", "boletas"),
             ("Confianza", "confianza"), ("Lift", "lift")]]
    return df.to_dict("records"), cols


# ================= Administrar planos =================
@app.callback(Output("admin-tienda", "options"), Output("admin-tienda", "value"), Input("url", "pathname"))
def _admin_init(pathname):
    if pathname != "/admin":
        return dash.no_update, dash.no_update
    _lazy_init()
    tiendas = db.get_tiendas()
    con_plano = set(db.tiendas_con_plano())
    opts = [{"label": f"{r.cod_tienda} {'✅' if r.cod_tienda in con_plano else ''}", "value": r.cod_tienda}
            for r in tiendas.itertuples()]
    return opts, (tiendas["cod_tienda"].iloc[0] if len(tiendas) else None)


@app.callback(Output("admin-imagen-preview", "children"), Output("admin-imagen-store", "data"),
              Input("admin-upload-imagen", "contents"))
def _admin_preview_imagen(contents):
    if not contents:
        return None, None
    return html.Img(src=contents, style={"maxWidth": "100%", "borderRadius": "8px", "marginTop": "10px"}), contents


@app.callback(Output("admin-plano-msg", "children"),
              Input("admin-guardar-plano", "n_clicks"),
              State("admin-imagen-store", "data"), State("admin-tienda", "value"), prevent_initial_call=True)
def _admin_guardar_plano(n, contents, tienda):
    if not contents or not tienda:
        return dbc.Alert("Primero sube una imagen.", color="warning")
    from PIL import Image
    import io as _io
    header, b64data = contents.split(",", 1)
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
    header, b64data = contents.split(",", 1)
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
    return cp.to_dict("records"), cols, cr.to_dict("records"), cols


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 8050)))

