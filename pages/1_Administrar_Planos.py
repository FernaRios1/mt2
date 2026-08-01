import streamlit as st
import pandas as pd
from PIL import Image
import io

import db
from auth import gate

st.set_page_config(page_title="Administrar Planos — Imperial", layout="wide", page_icon="🗺️")
gate()
db.ensure_ready()

st.title("🗺️ Administrar planos de tienda")
st.caption(
    "Sube el plano (imagen) y las coordenadas de cada pasillo. Una vez cargados, "
    "el mapa de calor de la tienda aparece solo en el panel principal — no hace falta tocar código."
)

tiendas = db.get_tiendas()
if tiendas.empty:
    st.error("No hay tiendas cargadas todavía — corre primero el agente de sincronización.")
    st.stop()

con_plano = set(db.tiendas_con_plano())
tiendas_fmt = {
    r.cod_tienda: f"{r.cod_tienda} {'✅ ya tiene plano' if r.cod_tienda in con_plano else '— sin plano'}"
    for r in tiendas.itertuples()
}
cod_tienda = st.selectbox("Tienda", tiendas["cod_tienda"], format_func=lambda c: tiendas_fmt[c])

st.divider()
col1, col2 = st.columns(2)

with col1:
    st.subheader("1. Imagen del plano")
    img_file = st.file_uploader("Plano en PNG o JPG", type=["png", "jpg", "jpeg"])
    if img_file is not None:
        img = Image.open(img_file)
        st.image(img, caption=f"{img.width} × {img.height} px", width='stretch')
        if st.button("Guardar plano", type="primary"):
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            db.guardar_plano(cod_tienda, buf.getvalue(), img.width, img.height)
            st.success("Plano guardado.")
            st.cache_data.clear()

with col2:
    st.subheader("2. Coordenadas")
    nivel = st.radio("Nivel de detalle", ["Pasillo", "Rack"], horizontal=True)
    nivel_key = "pasillo" if nivel == "Pasillo" else "rack"
    st.markdown(
        f"Sube un CSV con columnas **{nivel_key}, x, y** — x e y son la posición en píxeles "
        "sobre la imagen que subiste al lado (esquina superior izquierda = 0,0). "
        "Si no las tienes medidas, puedes estimarlas abriendo la imagen en cualquier editor "
        "y viendo la posición del cursor. Pasillo da un mapa de calor más general; "
        "Rack da el detalle fino (requiere más puntos)."
    )
    csv_file = st.file_uploader("Coordenadas (CSV)", type=["csv"], key=f"coords_{nivel_key}")
    if csv_file is not None:
        df_coords = pd.read_csv(csv_file)
        df_coords.columns = [c.lower() for c in df_coords.columns]
        faltan = {nivel_key, "x", "y"} - set(df_coords.columns)
        if faltan:
            st.error(f"Faltan columnas: {', '.join(faltan)}")
        else:
            st.dataframe(df_coords, width='stretch', height=200)
            if st.button("Guardar coordenadas", type="primary"):
                db.guardar_coords(cod_tienda, df_coords, nivel=nivel_key)
                st.success(f"{len(df_coords)} filas de {nivel_key} guardadas para {cod_tienda}.")
                st.cache_data.clear()

st.divider()
st.subheader("Coordenadas actuales")
col_p, col_r = st.columns(2)
with col_p:
    st.caption("Por pasillo")
    actuales_p = db.get_coords(cod_tienda, nivel="pasillo")
    st.dataframe(actuales_p, width='stretch', hide_index=True) if not actuales_p.empty else st.caption("Sin datos.")
with col_r:
    st.caption("Por rack")
    actuales_r = db.get_coords(cod_tienda, nivel="rack")
    st.dataframe(actuales_r, width='stretch', hide_index=True) if not actuales_r.empty else st.caption("Sin datos.")
