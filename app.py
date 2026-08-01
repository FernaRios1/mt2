import streamlit as st
import plotly.graph_objects as go
import base64

import db
from auth import gate

st.set_page_config(page_title="Rentabilidad Rack — Imperial", layout="wide", page_icon="🔧")
gate()

with st.spinner("Preparando la base de datos..."):
    estado_db = db.ensure_ready()
if estado_db != "ya estaba lista":
    st.toast(f"Base de datos inicializada por primera vez: {estado_db}", icon="✅")

# ---------- estilo ----------
st.markdown("""
<link href="https://fonts.googleapis.com/css2?family=Archivo+Expanded:wght@700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
html, body, [class*="css"] { font-family: 'Barlow', -apple-system, sans-serif; }

/* --- sidebar oscuro tipo panel de control --- */
[data-testid="stSidebar"] {
    background: #16212B;
}
[data-testid="stSidebar"] * { color: #DDE3E8 !important; }
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
    font-family: 'Archivo Expanded', sans-serif; color: #FFFFFF !important;
}

/* --- contraste de selects/multiselects en el sidebar (Tienda, Mes, filtros) ---
   El control cerrado vive dentro del sidebar, pero BaseWeb pinta fondos claros
   en divs internos que la regla de arriba no cubre (solo el color de texto);
   forzamos fondo transparente en todos los descendientes y el color oscuro
   solo en el contenedor visible, para que el texto blanco quede sobre fondo
   oscuro y no sobre el blanco por defecto. */
[data-testid="stSidebar"] [data-baseweb="select"] * { background: transparent !important; color: #FFFFFF !important; }
[data-testid="stSidebar"] [data-baseweb="select"] > div {
    background: #223040 !important; border-color: #2E3D4D !important;
}
[data-testid="stSidebar"] [data-baseweb="tag"] { background: #2E4258 !important; }
[data-testid="stSidebar"] [data-baseweb="tag"] * { color: #FFFFFF !important; }
/* --- la lista de opciones se monta fuera del sidebar (portal), hay que
   oscurecerla aparte para que el texto no quede blanco-sobre-blanco --- */
[data-baseweb="popover"] { background: #16212B !important; }
[data-baseweb="popover"] li, [data-baseweb="menu"] li, [data-baseweb="popover"] div {
    background: #16212B !important; color: #FFFFFF !important;
}
[data-baseweb="popover"] li:hover { background: #223040 !important; }

[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,.12); }

/* --- tipografia de titulos y numeros --- */
h1, h2, h3 { font-family: 'Archivo Expanded', sans-serif; letter-spacing: -.01em; }
div[data-testid="stMetricValue"] { font-family: 'JetBrains Mono', monospace; font-weight: 600; }
div[data-testid="stMetric"] {
    background: #FFFFFF; border: 1px solid #DCE1E0; border-radius: 10px; padding: 14px 16px 8px;
}

/* --- badges --- */
.badge-bad { background:#F6E4E1;color:#C4432B;padding:3px 10px;border-radius:20px;font-size:11.5px;font-weight:700;text-transform:uppercase;letter-spacing:.03em; }
.badge-good { background:#E4F1E9;color:#1E8A5B;padding:3px 10px;border-radius:20px;font-size:11.5px;font-weight:700;text-transform:uppercase;letter-spacing:.03em; }
.badge-neutral { background:#EDEFF1;color:#5B6B79;padding:3px 10px;border-radius:20px;font-size:11.5px;font-weight:700;text-transform:uppercase;letter-spacing:.03em; }

/* --- tablas mas compactas y prolijas --- */
[data-testid="stDataFrame"] { border: 1px solid #DCE1E0; border-radius: 8px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)

fmt = lambda n: f"${n:,.0f}".replace(",", ".")
fmt_stock = lambda v: "Sí" if v == "S" else ("No" if v == "N" else "—")

# ---------- sidebar: tienda, período y filtros ----------
tiendas = db.get_tiendas()
if tiendas.empty:
    st.error("No hay tiendas cargadas todavía en la base.")
    st.stop()

st.sidebar.markdown("## 🔧 RENTABILIDAD RACK")
st.sidebar.caption("Imperial Ferretería · venta por sección")

cod_tienda = st.sidebar.selectbox(
    "Tienda", tiendas["cod_tienda"],
    format_func=lambda c: f"{c} — {tiendas.set_index('cod_tienda').loc[c,'tipo']}",
)

meses, semanas = db.get_periodos(cod_tienda)
if meses.empty:
    st.warning(f"Todavía no hay datos cargados para {cod_tienda}.")
    st.stop()

anio = int(meses["anio"].iloc[-1])
st.sidebar.divider()
modo_periodo = st.sidebar.radio("Ver por", ["Mes", "Semana", "Año completo"])

mes_sel = None
semana_sel = None
if modo_periodo == "Mes":
    meses_disp = meses[meses["anio"] == anio]["mes"].tolist()
    mes_sel = st.sidebar.selectbox("Mes", meses_disp, index=len(meses_disp) - 1)
elif modo_periodo == "Semana":
    semanas_disp = semanas[semanas["anio"] == anio]["semana"].tolist()
    semana_sel = st.sidebar.selectbox("Semana del año", semanas_disp, index=len(semanas_disp) - 1)

st.sidebar.divider()
st.sidebar.markdown("### Filtros de producto")
opciones = db.get_opciones_filtro(cod_tienda)

filtros = {}
filtros["familia"] = st.sidebar.multiselect("Familia", opciones["familia"])
filtros["categoria"] = st.sidebar.multiselect("Categoría", opciones["categoria"])
filtros["clasificacion"] = st.sidebar.multiselect("Clasificación SKU", opciones["clasificacion"])
filtros["zona_pck"] = st.sidebar.multiselect("Zona de picking", opciones["zona_pck"])
filtros["responsable_linea"] = st.sidebar.multiselect("Jefe de línea", opciones["responsable_linea"])
maneja_sel = st.sidebar.radio("Maneja stock", ["Todos", "Sí", "No"], horizontal=True)
filtros["maneja_stock"] = ["S"] if maneja_sel == "Sí" else ["N"] if maneja_sel == "No" else []
filtros = {k: v for k, v in filtros.items() if v}

st.sidebar.divider()
st.sidebar.caption("Datos sincronizados por el agente de escritorio — no en vivo minuto a minuto.")

# ---------- selección activa desde el mapa (clic en un punto) ----------
if "mapa_key_n" not in st.session_state:
    st.session_state["mapa_key_n"] = 0

pasillo_sel = st.session_state.get("pasillo_sel")
rack_sel = st.session_state.get("rack_sel")
seleccion_activa = pasillo_sel or rack_sel
nivel_seleccion = "pasillo" if pasillo_sel else ("rack" if rack_sel else None)

# ---------- datos del período (ya cruzados por la selección del mapa, si hay) ----------
pasillos = db.get_pasillo_resumen(cod_tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                                   pasillo_sel=pasillo_sel, rack_sel=rack_sel)
racks = db.get_rack_detalle(cod_tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros,
                             pasillo_sel=pasillo_sel, rack_sel=rack_sel)
top = db.get_top_productos(cod_tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros, n=50,
                            pasillo_sel=pasillo_sel, rack_sel=rack_sel)
baja = db.get_top_productos(cod_tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros, n=50,
                             ascendente=True, pasillo_sel=pasillo_sel, rack_sel=rack_sel)
sin_venta = db.get_sin_venta(cod_tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros, n=300)

tipo_tienda = tiendas.set_index("cod_tienda").loc[cod_tienda, "tipo"]
periodo_txt = (f"Semana {semana_sel}" if semana_sel else
               f"Mes {mes_sel}/{anio}" if mes_sel else f"Año {anio}")

st.markdown(f"# {cod_tienda}")
st.markdown(
    f'<span class="badge-good">{tipo_tienda}</span> &nbsp; <span style="color:#5B6B79">{periodo_txt}'
    + (f" · {sum(len(v) for v in filtros.values())} filtro(s) activo(s)" if filtros else "") + "</span>",
    unsafe_allow_html=True,
)

if seleccion_activa:
    colf1, colf2 = st.columns([5, 1])
    with colf1:
        st.info(f"🔎 Filtrando todas las tablas por **{nivel_seleccion}: {seleccion_activa}** "
                "(clic en el mapa de abajo).")
    with colf2:
        if st.button("Quitar filtro del mapa", width="stretch"):
            st.session_state["pasillo_sel"] = None
            st.session_state["rack_sel"] = None
            st.session_state["mapa_key_n"] += 1
            st.rerun()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Venta del período", fmt(pasillos["venta"].sum()) if len(pasillos) else "$0")
c2.metric("Pasillos con venta", int(pasillos["pasillo"].nunique()) if len(pasillos) else 0)
c3.metric("Racks con venta", int(racks["rack"].nunique()) if len(racks) else 0)
c4.metric("SKU con stock sin venta", len(sin_venta), help="Se corta en 300 para no sobrecargar la página")

if pasillos["margen"].sum() == 0:
    st.warning("⚠️ Margen en $0 — bug conocido en el JOIN de costos del origen de datos. La venta sí es real.")

st.divider()

# ---------- mapa de calor sobre el plano ----------
col_h1, col_h2 = st.columns([3, 1])
with col_h1:
    st.subheader("Venta sobre el plano")
    st.caption("Clic en un punto para filtrar todas las tablas de abajo por ese pasillo o rack.")
with col_h2:
    nivel_mapa = st.radio("Nivel", ["Pasillo", "Rack"], horizontal=True, label_visibility="collapsed")
nivel_mapa = "pasillo" if nivel_mapa == "Pasillo" else "rack"

plano = db.get_plano(cod_tienda)
coords_mapa = db.get_coords(cod_tienda, nivel=nivel_mapa)

if plano is None or coords_mapa.empty:
    st.info(
        f"Todavía no hay coordenadas de **{nivel_mapa}** para **{cod_tienda}**. "
        "Ve a la página **Administrar Planos** (menú de la izquierda) para subirlas."
    )
else:
    # el mapa siempre se pinta con la venta del período/filtros de producto,
    # SIN aplicar la propia selección de pasillo/rack (para no perder de
    # vista el resto del plano al hacer clic en un punto)
    venta_nivel = db.get_venta_por_nivel(cod_tienda, anio, mes=mes_sel, semana=semana_sel,
                                          filtros=filtros, nivel=nivel_mapa)
    venta_map = venta_nivel.set_index("clave")["venta"].to_dict()
    coords_mapa = coords_mapa.copy()
    coords_mapa["venta"] = coords_mapa["clave"].map(venta_map).fillna(0)
    max_v = max(coords_mapa["venta"].max(), 1)

    img_b64 = base64.b64encode(plano["imagen"]).decode("ascii")
    fig = go.Figure()
    fig.add_layout_image(
        dict(source=f"data:image/png;base64,{img_b64}", xref="x", yref="y",
             x=0, y=0, sizex=plano["img_w"], sizey=plano["img_h"], sizing="stretch", layer="below")
    )
    fig.add_trace(go.Scatter(
        x=coords_mapa["x"], y=coords_mapa["y"], mode="markers",
        marker=dict(
            size=8 + 30 * (coords_mapa["venta"] / max_v) ** 0.5,
            color=coords_mapa["venta"], colorscale="Turbo", showscale=True, colorbar=dict(title="Venta"),
            line=dict(width=1.5, color="white"), cmin=0, cmax=max_v,
        ),
        text=[f"{nivel_mapa.capitalize()} {c}<br>{fmt(v)}" for c, v in zip(coords_mapa["clave"], coords_mapa["venta"])],
        hoverinfo="text",
    ))
    fig.update_xaxes(visible=False, range=[0, plano["img_w"]])
    fig.update_yaxes(visible=False, range=[plano["img_h"], 0])
    fig.update_layout(height=550, margin=dict(l=0, r=0, t=10, b=0), plot_bgcolor="white")

    evento_mapa = st.plotly_chart(
        fig, width="stretch", on_select="rerun", selection_mode="points",
        key=f"mapa_calor_{st.session_state['mapa_key_n']}",
    )
    if evento_mapa and evento_mapa.get("selection", {}).get("points"):
        puntos = evento_mapa["selection"]["points"]
        if puntos:
            idx = puntos[0].get("point_index")
            if idx is not None and idx < len(coords_mapa):
                clave = str(coords_mapa.iloc[idx]["clave"])
                nuevo_pasillo = clave if nivel_mapa == "pasillo" else None
                nuevo_rack = clave if nivel_mapa == "rack" else None
                if nuevo_pasillo != pasillo_sel or nuevo_rack != rack_sel:
                    st.session_state["pasillo_sel"] = nuevo_pasillo
                    st.session_state["rack_sel"] = nuevo_rack
                    st.rerun()

st.divider()

# ---------- tabla pasillo/rack ----------
col_a, col_b = st.columns([1, 1])
with col_a:
    st.subheader("Venta por pasillo")
    st.dataframe(pasillos, width="stretch", hide_index=True,
                 column_config={"venta": st.column_config.NumberColumn(format="$%d"),
                                 "margen": st.column_config.NumberColumn(format="$%d")})

with col_b:
    st.subheader("Detalle por rack")
    st.caption("Recomendación = venta del rack vs. el promedio de los racks visibles en esta vista "
               "(≥1.5× el promedio → aumentar espacio; ≤0.5× → reducir).")
    filtro_txt = st.text_input("Filtrar por pasillo o rack")
    racks_f = racks[racks["pasillo"].str.contains(filtro_txt, case=False, na=False) |
                     racks["rack"].str.contains(filtro_txt, case=False, na=False)] if filtro_txt else racks
    racks_f = racks_f.copy()
    if len(racks_f):
        promedio_racks = racks_f["venta"].mean()

        def _clasificar_espacio(v):
            if promedio_racks <= 0:
                return "Sin datos"
            if v >= promedio_racks * 1.5:
                return "Aumentar espacio"
            if v <= promedio_racks * 0.5:
                return "Reducir espacio"
            return "Mantener"

        racks_f["recomendación"] = racks_f["venta"].apply(_clasificar_espacio)

        def _color_reco(val):
            if val == "Aumentar espacio":
                return "background-color:#E4F1E9;color:#1E8A5B;font-weight:600;"
            if val == "Reducir espacio":
                return "background-color:#F6E4E1;color:#C4432B;font-weight:600;"
            return "color:#5B6B79;"

        estilo = (racks_f.style
                  .applymap(_color_reco, subset=["recomendación"])
                  .format({"venta": fmt, "margen": fmt}))
        st.dataframe(estilo, width="stretch", hide_index=True, height=350)
    else:
        st.caption("Sin racks para este filtro.")

st.divider()

# ---------- resumen jerárquico: familia -> jefe de línea -> subconjunto ----------
st.subheader("Resumen por familia → jefe de línea → subconjunto")
jerarquia = db.get_resumen_jerarquico(cod_tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros)
if jerarquia.empty:
    st.caption("Sin datos para este período/filtros.")
else:
    tot_familia = jerarquia.groupby("familia")["venta"].sum().sort_values(ascending=False)
    for familia_val in tot_familia.index:
        g_fam = jerarquia[jerarquia["familia"] == familia_val]
        with st.expander(f"{familia_val} — {fmt(tot_familia[familia_val])}"):
            tot_jefe = g_fam.groupby("jefe_linea")["venta"].sum().sort_values(ascending=False)
            for jefe_val in tot_jefe.index:
                g_jefe = g_fam[g_fam["jefe_linea"] == jefe_val].sort_values("venta", ascending=False)
                st.markdown(f"**{jefe_val}** — {fmt(tot_jefe[jefe_val])}")
                st.dataframe(g_jefe[["subconjunto", "venta", "margen"]], width="stretch", hide_index=True,
                             column_config={"venta": st.column_config.NumberColumn(format="$%d"),
                                             "margen": st.column_config.NumberColumn(format="$%d")})

st.divider()

# ---------- productos ----------
if "maneja_stock" in top.columns:
    top["maneja_stock"] = top["maneja_stock"].map(fmt_stock)
if "maneja_stock" in baja.columns:
    baja["maneja_stock"] = baja["maneja_stock"].map(fmt_stock)

col_c, col_d = st.columns(2)
with col_c:
    st.subheader("Top productos")
    st.dataframe(top, width="stretch", hide_index=True,
                 column_config={
                     "venta": st.column_config.NumberColumn(format="$%d"),
                     "maneja_stock": st.column_config.TextColumn("Maneja stock"),
                 })
    st.download_button("⬇ Descargar CSV", top.to_csv(index=False).encode("utf-8"),
                        file_name=f"top_productos_{cod_tienda}_{periodo_txt}.csv", mime="text/csv")

with col_d:
    st.subheader("Menor venta (con transacciones)")
    st.dataframe(baja, width="stretch", hide_index=True,
                 column_config={
                     "venta": st.column_config.NumberColumn(format="$%d"),
                     "maneja_stock": st.column_config.TextColumn("Maneja stock"),
                 })
    st.download_button("⬇ Descargar CSV", baja.to_csv(index=False).encode("utf-8"),
                        file_name=f"menor_venta_{cod_tienda}_{periodo_txt}.csv", mime="text/csv")

st.caption("La columna \"Maneja stock\" indica si el SKU está marcado como Sí/No en el origen de datos; "
           "la cantidad exacta en bodega todavía no está en el modelo (ver nota abajo).")

st.subheader("Con stock, sin venta este período")
st.markdown('<span class="badge-bad">candidatos a cambiar</span>', unsafe_allow_html=True)
st.dataframe(sin_venta, width="stretch", hide_index=True)
st.download_button("⬇ Descargar CSV", sin_venta.to_csv(index=False).encode("utf-8"),
                    file_name=f"sin_venta_{cod_tienda}_{periodo_txt}.csv", mime="text/csv")

st.divider()

# ---------- productos / secciones que no están mapeadas en el plano ----------
st.subheader("Sin asociar al plano")
st.caption("Pasillos o racks con venta en este período/filtros que todavía no tienen coordenada cargada "
           "-- por eso no aparecen como punto en el mapa de arriba. Súbelas en Administrar Planos.")
col_sp1, col_sp2 = st.columns(2)
with col_sp1:
    st.markdown("**Pasillos sin coordenada**")
    sin_pas = db.get_sin_coord(cod_tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros, nivel="pasillo")
    if sin_pas.empty:
        st.markdown('<span class="badge-good">todos los pasillos con venta están mapeados</span>',
                     unsafe_allow_html=True)
    else:
        st.dataframe(sin_pas, width="stretch", hide_index=True,
                     column_config={"venta": st.column_config.NumberColumn(format="$%d")})
with col_sp2:
    st.markdown("**Racks sin coordenada**")
    sin_rack = db.get_sin_coord(cod_tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros, nivel="rack")
    if sin_rack.empty:
        st.markdown('<span class="badge-good">todos los racks con venta están mapeados</span>',
                     unsafe_allow_html=True)
    else:
        st.dataframe(sin_rack, width="stretch", hide_index=True,
                     column_config={"venta": st.column_config.NumberColumn(format="$%d")})
