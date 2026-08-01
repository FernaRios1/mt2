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
[data-testid="stSidebar"] [data-baseweb="select"] > div { background: #223040; border-color: #2E3D4D; }
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

/* --- tablas mas compactas y prolijas --- */
[data-testid="stDataFrame"] { border: 1px solid #DCE1E0; border-radius: 8px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)

fmt = lambda n: f"${n:,.0f}".replace(",", ".")

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

# ---------- datos del período ----------
pasillos = db.get_pasillo_resumen(cod_tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros)
racks = db.get_rack_detalle(cod_tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros)
top = db.get_top_productos(cod_tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros, n=50)
baja = db.get_top_productos(cod_tienda, anio, mes=mes_sel, semana=semana_sel, filtros=filtros, n=50, ascendente=True)
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
with col_h2:
    nivel_mapa = st.radio("Nivel", ["Pasillo", "Rack"], horizontal=True, label_visibility="collapsed")
nivel_mapa = "pasillo" if nivel_mapa == "Pasillo" else "rack"

plano = db.get_plano(cod_tienda)
coords = db.get_coords(cod_tienda, nivel=nivel_mapa)

if plano is None or coords.empty:
    st.info(
        f"Todavía no hay coordenadas de **{nivel_mapa}** para **{cod_tienda}**. "
        "Ve a la página **Administrar Planos** (menú de la izquierda) para subirlas."
    )
else:
    venta_nivel = db.get_venta_por_nivel(cod_tienda, anio, mes=mes_sel, semana=semana_sel,
                                          filtros=filtros, nivel=nivel_mapa)
    venta_map = venta_nivel.set_index("clave")["venta"].to_dict()
    coords = coords.copy()
    coords["venta"] = coords["clave"].map(venta_map).fillna(0)
    max_v = max(coords["venta"].max(), 1)

    img_b64 = base64.b64encode(plano["imagen"]).decode("ascii")
    fig = go.Figure()
    fig.add_layout_image(
        dict(source=f"data:image/png;base64,{img_b64}", xref="x", yref="y",
             x=0, y=0, sizex=plano["img_w"], sizey=plano["img_h"], sizing="stretch", layer="below")
    )
    fig.add_trace(go.Scatter(
        x=coords["x"], y=coords["y"], mode="markers",
        marker=dict(
            size=8 + 30 * (coords["venta"] / max_v) ** 0.5,
            color=coords["venta"], colorscale="YlOrRd", showscale=True, colorbar=dict(title="Venta"),
            line=dict(width=1, color="white"),
        ),
        text=[f"{nivel_mapa.capitalize()} {c}<br>{fmt(v)}" for c, v in zip(coords["clave"], coords["venta"])],
        hoverinfo="text",
    ))
    fig.update_xaxes(visible=False, range=[0, plano["img_w"]])
    fig.update_yaxes(visible=False, range=[plano["img_h"], 0])
    fig.update_layout(height=550, margin=dict(l=0, r=0, t=10, b=0), plot_bgcolor="white")
    st.plotly_chart(fig, width="stretch")

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
    filtro_txt = st.text_input("Filtrar por pasillo o rack")
    racks_f = racks[racks["pasillo"].str.contains(filtro_txt, case=False, na=False) |
                     racks["rack"].str.contains(filtro_txt, case=False, na=False)] if filtro_txt else racks
    st.dataframe(racks_f, width="stretch", hide_index=True, height=350,
                 column_config={"venta": st.column_config.NumberColumn(format="$%d"),
                                 "margen": st.column_config.NumberColumn(format="$%d")})

st.divider()

# ---------- productos ----------
col_c, col_d = st.columns(2)
with col_c:
    st.subheader("Top productos")
    st.dataframe(top, width="stretch", hide_index=True,
                 column_config={"venta": st.column_config.NumberColumn(format="$%d")})
    st.download_button("⬇ Descargar CSV", top.to_csv(index=False).encode("utf-8"),
                        file_name=f"top_productos_{cod_tienda}_{periodo_txt}.csv", mime="text/csv")

with col_d:
    st.subheader("Menor venta (con transacciones)")
    st.dataframe(baja, width="stretch", hide_index=True,
                 column_config={"venta": st.column_config.NumberColumn(format="$%d")})
    st.download_button("⬇ Descargar CSV", baja.to_csv(index=False).encode("utf-8"),
                        file_name=f"menor_venta_{cod_tienda}_{periodo_txt}.csv", mime="text/csv")

st.subheader("Con stock, sin venta este período")
st.markdown('<span class="badge-bad">candidatos a cambiar</span>', unsafe_allow_html=True)
st.dataframe(sin_venta, width="stretch", hide_index=True)
st.download_button("⬇ Descargar CSV", sin_venta.to_csv(index=False).encode("utf-8"),
                    file_name=f"sin_venta_{cod_tienda}_{periodo_txt}.csv", mime="text/csv")
