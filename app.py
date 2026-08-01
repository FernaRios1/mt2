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

# ---------- estilo mínimo ----------
st.markdown("""
<style>
div[data-testid="stMetricValue"] { font-family: 'JetBrains Mono', monospace; }
.badge-bad { background:#F6E4E1;color:#C4432B;padding:2px 8px;border-radius:12px;font-size:12px;font-weight:600; }
</style>
""", unsafe_allow_html=True)

fmt = lambda n: f"${n:,.0f}".replace(",", ".")

# ---------- sidebar: tienda y período ----------
tiendas = db.get_tiendas()
if tiendas.empty:
    st.error("No hay tiendas cargadas todavía en la base. Corre el agente de sincronización primero.")
    st.stop()

st.sidebar.title("🔧 Rentabilidad Rack")
cod_tienda = st.sidebar.selectbox(
    "Tienda",
    tiendas["cod_tienda"],
    format_func=lambda c: f"{c} — {tiendas.set_index('cod_tienda').loc[c,'tipo']}",
)

meses, semanas = db.get_periodos(cod_tienda)
if meses.empty:
    st.warning(f"Todavía no hay datos cargados para {cod_tienda}.")
    st.stop()

anio = int(meses["anio"].iloc[-1])
modo_periodo = st.sidebar.radio("Ver por", ["Mes", "Semana", "Año completo"], horizontal=False)

mes_sel = None
semana_sel = None
if modo_periodo == "Mes":
    meses_disp = meses[meses["anio"] == anio]["mes"].tolist()
    mes_sel = st.sidebar.selectbox("Mes", meses_disp, index=len(meses_disp) - 1)
elif modo_periodo == "Semana":
    semanas_disp = semanas[semanas["anio"] == anio]["semana"].tolist()
    semana_sel = st.sidebar.selectbox("Semana del año", semanas_disp, index=len(semanas_disp) - 1)

st.sidebar.caption("Los datos se sincronizan con el agente de escritorio — no son en vivo minuto a minuto.")

# ---------- datos del período ----------
pr = db.get_pasillo_rack(cod_tienda, anio, mes=mes_sel, semana=semana_sel)
pasillos = db.get_pasillo_resumen(cod_tienda, anio, mes=mes_sel, semana=semana_sel)
top = db.get_top_productos(cod_tienda, anio, mes=mes_sel, semana=semana_sel, n=50)
baja = db.get_top_productos(cod_tienda, anio, mes=mes_sel, semana=semana_sel, n=50, ascendente=True)
sin_venta = db.get_sin_venta(cod_tienda, anio, mes=mes_sel, semana=semana_sel, n=300)

tipo_tienda = tiendas.set_index("cod_tienda").loc[cod_tienda, "tipo"]
periodo_txt = (f"Semana {semana_sel}" if semana_sel else
               f"Mes {mes_sel}/{anio}" if mes_sel else f"Año {anio}")

st.title(f"{cod_tienda}  ·  {tipo_tienda}")
st.caption(periodo_txt)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Venta del período", fmt(pr["venta"].sum()) if len(pr) else "$0")
c2.metric("Pasillos con venta", int(pasillos["pasillo"].nunique()) if len(pasillos) else 0)
c3.metric("Racks con venta", int(pr["rack"].nunique()) if len(pr) else 0)
c4.metric("SKU con stock sin venta", len(sin_venta), help="Puede haber más — la lista se corta en 300 para no sobrecargar la página")

if pr["margen"].sum() == 0:
    st.warning("⚠️ Margen en $0 — bug conocido en el JOIN de costos del origen de datos. La venta sí es real.")

st.divider()

# ---------- mapa de calor sobre el plano ----------
st.subheader("Venta por pasillo sobre el plano")
plano = db.get_plano(cod_tienda)
coords = db.get_coords(cod_tienda)

if plano is None or coords.empty:
    st.info(
        f"Todavía no hay plano cargado para **{cod_tienda}**. "
        "Ve a la página **Administrar Planos** (menú de la izquierda) para subirlo — "
        "una vez subido, esta vista se arma sola."
    )
else:
    venta_por_pasillo = pasillos.set_index("pasillo")["venta"].to_dict()
    coords = coords.copy()
    coords["venta"] = coords["pasillo"].map(venta_por_pasillo).fillna(0)
    max_v = max(coords["venta"].max(), 1)

    img_b64 = base64.b64encode(plano["imagen"]).decode("ascii")
    fig = go.Figure()
    fig.add_layout_image(
        dict(source=f"data:image/png;base64,{img_b64}", xref="x", yref="y",
             x=0, y=0, sizex=plano["img_w"], sizey=plano["img_h"],
             sizing="stretch", layer="below")
    )
    fig.add_trace(go.Scatter(
        x=coords["x"], y=coords["y"], mode="markers",
        marker=dict(
            size=10 + 34 * (coords["venta"] / max_v) ** 0.5,
            color=coords["venta"], colorscale="YlOrRd", showscale=True,
            colorbar=dict(title="Venta"),
            line=dict(width=1, color="white"),
        ),
        text=[f"Pasillo {p}<br>{fmt(v)}" for p, v in zip(coords["pasillo"], coords["venta"])],
        hoverinfo="text",
    ))
    fig.update_xaxes(visible=False, range=[0, plano["img_w"]])
    fig.update_yaxes(visible=False, range=[plano["img_h"], 0])  # invertido, como en el plano
    fig.update_layout(height=550, margin=dict(l=0, r=0, t=10, b=0),
                       plot_bgcolor="white")
    st.plotly_chart(fig, width='stretch')

st.divider()

# ---------- tabla pasillo/rack ----------
col_a, col_b = st.columns([1, 1])
with col_a:
    st.subheader("Venta por pasillo")
    st.dataframe(pasillos, width='stretch', hide_index=True,
                 column_config={"venta": st.column_config.NumberColumn(format="$%d"),
                                 "margen": st.column_config.NumberColumn(format="$%d")})

with col_b:
    st.subheader("Detalle por rack")
    filtro = st.text_input("Filtrar por pasillo o rack")
    pr_f = pr[pr["pasillo"].str.contains(filtro, case=False, na=False) |
              pr["rack"].str.contains(filtro, case=False, na=False)] if filtro else pr
    st.dataframe(pr_f, width='stretch', hide_index=True, height=350,
                 column_config={"venta": st.column_config.NumberColumn(format="$%d"),
                                 "margen": st.column_config.NumberColumn(format="$%d")})

st.divider()

# ---------- productos ----------
col_c, col_d = st.columns(2)
with col_c:
    st.subheader("Top productos")
    st.dataframe(top, width='stretch', hide_index=True,
                 column_config={"venta": st.column_config.NumberColumn(format="$%d")})
    st.download_button("⬇ Descargar CSV", top.to_csv(index=False).encode("utf-8"),
                        file_name=f"top_productos_{cod_tienda}_{periodo_txt}.csv", mime="text/csv")

with col_d:
    st.subheader("Menor venta (con transacciones)")
    st.dataframe(baja, width='stretch', hide_index=True,
                 column_config={"venta": st.column_config.NumberColumn(format="$%d")})
    st.download_button("⬇ Descargar CSV", baja.to_csv(index=False).encode("utf-8"),
                        file_name=f"menor_venta_{cod_tienda}_{periodo_txt}.csv", mime="text/csv")

st.subheader("Con stock, sin venta este período  " )
st.markdown('<span class="badge-bad">candidatos a cambiar</span>', unsafe_allow_html=True)
st.dataframe(sin_venta, width='stretch', hide_index=True)
st.download_button("⬇ Descargar CSV", sin_venta.to_csv(index=False).encode("utf-8"),
                    file_name=f"sin_venta_{cod_tienda}_{periodo_txt}.csv", mime="text/csv")
