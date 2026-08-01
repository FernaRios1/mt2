"""Conexión y queries a Postgres para el panel Rentabilidad Rack."""
import os
import io
import pandas as pd
import psycopg2
import psycopg2.extras
import streamlit as st


def get_conn():
    """Conexión a Postgres. En Railway, DATABASE_URL viene inyectada sola
    si agregas el plugin de Postgres al mismo proyecto."""
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        # Railway entrega postgres:// -- psycopg2 acepta ambos, pero por si acaso:
        dsn = dsn.replace("postgres://", "postgresql://", 1)
        return psycopg2.connect(dsn)
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "rentabilidad"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", ""),
    )


def _df(query, params=None):
    with get_conn() as cn:
        return pd.read_sql(query, cn, params=params)


@st.cache_data(ttl=300)
def get_tiendas():
    return _df("SELECT cod_tienda, nombre, tipo FROM dim_tienda ORDER BY cod_tienda")


@st.cache_data(ttl=300)
def get_periodos(cod_tienda):
    """Meses y semanas disponibles para esa tienda (para poblar los filtros)."""
    meses = _df(
        "SELECT DISTINCT anio, mes FROM fact_pasillo_rack_semana "
        "WHERE cod_tienda=%(t)s ORDER BY anio, mes",
        {"t": cod_tienda},
    )
    semanas = _df(
        "SELECT DISTINCT anio, mes, semana FROM fact_pasillo_rack_semana "
        "WHERE cod_tienda=%(t)s ORDER BY anio, semana",
        {"t": cod_tienda},
    )
    return meses, semanas


def _periodo_where(cod_tienda, anio, mes, semana):
    """Arma el filtro de periodo: por semana si viene, si no por mes, si no todo el año."""
    where = "cod_tienda=%(t)s AND anio=%(a)s"
    params = {"t": cod_tienda, "a": anio}
    if semana is not None:
        where += " AND semana=%(s)s"
        params["s"] = semana
    elif mes is not None:
        where += " AND mes=%(m)s"
        params["m"] = mes
    return where, params


@st.cache_data(ttl=300)
def get_pasillo_rack(cod_tienda, anio, mes=None, semana=None):
    where, params = _periodo_where(cod_tienda, anio, mes, semana)
    q = f"""
        SELECT pasillo, rack, SUM(venta) AS venta, SUM(margen) AS margen
        FROM fact_pasillo_rack_semana
        WHERE {where}
        GROUP BY pasillo, rack
        ORDER BY venta DESC
    """
    return _df(q, params)


@st.cache_data(ttl=300)
def get_pasillo_resumen(cod_tienda, anio, mes=None, semana=None):
    where, params = _periodo_where(cod_tienda, anio, mes, semana)
    q = f"""
        SELECT pasillo, SUM(venta) AS venta, SUM(margen) AS margen, COUNT(DISTINCT rack) AS racks
        FROM fact_pasillo_rack_semana
        WHERE {where}
        GROUP BY pasillo
        ORDER BY venta DESC
    """
    return _df(q, params)


@st.cache_data(ttl=300)
def get_top_productos(cod_tienda, anio, mes=None, semana=None, n=50, ascendente=False):
    where, params = _periodo_where(cod_tienda, anio, mes, semana)
    orden = "ASC" if ascendente else "DESC"
    extra = "AND venta > 0" if ascendente else ""
    params["n"] = n
    q = f"""
        SELECT cod_rapido, descripcion, SUM(venta) AS venta, SUM(cantidad) AS cantidad
        FROM fact_producto_semana
        WHERE {where}
        GROUP BY cod_rapido, descripcion
        HAVING SUM(venta) IS NOT NULL {("AND SUM(venta) > 0" if ascendente else "")}
        ORDER BY venta {orden}
        LIMIT %(n)s
    """
    return _df(q, params)


@st.cache_data(ttl=300)
def get_sin_venta(cod_tienda, anio, mes=None, semana=None, n=200):
    """Productos con stock que no aparecen con venta > 0 en el período elegido."""
    where, params = _periodo_where(cod_tienda, anio, mes, semana)
    params["n"] = n
    q = f"""
        SELECT d.cod_rapido, d.descripcion
        FROM dim_producto_tienda d
        WHERE d.cod_tienda = %(t)s AND d.maneja_stock = 'S'
          AND NOT EXISTS (
              SELECT 1 FROM fact_producto_semana f
              WHERE {where.replace('cod_tienda', 'f.cod_tienda').replace('anio','f.anio').replace('mes','f.mes').replace('semana','f.semana')}
                AND f.cod_rapido = d.cod_rapido AND f.venta > 0
          )
        ORDER BY d.descripcion
        LIMIT %(n)s
    """
    return _df(q, params)


@st.cache_data(ttl=600)
def get_plano(cod_tienda):
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute(
            "SELECT imagen, img_w, img_h FROM dim_plano WHERE cod_tienda=%s", (cod_tienda,)
        )
        row = cur.fetchone()
    if not row:
        return None
    imagen, w, h = row
    return {"imagen": bytes(imagen), "img_w": w, "img_h": h}


@st.cache_data(ttl=600)
def get_coords(cod_tienda):
    return _df(
        "SELECT pasillo, x, y FROM dim_pasillo_coord WHERE cod_tienda=%(t)s",
        {"t": cod_tienda},
    )


def guardar_plano(cod_tienda, imagen_bytes, img_w, img_h):
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dim_plano (cod_tienda, imagen, img_w, img_h, actualizado)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (cod_tienda) DO UPDATE
            SET imagen = EXCLUDED.imagen, img_w = EXCLUDED.img_w,
                img_h = EXCLUDED.img_h, actualizado = now()
            """,
            (cod_tienda, psycopg2.Binary(imagen_bytes), img_w, img_h),
        )
        cn.commit()


def guardar_coords(cod_tienda, df_coords):
    """df_coords: columnas pasillo, x, y. Filas sin x/y numérico se descartan."""
    df_coords = df_coords.dropna(subset=["x", "y"])
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute("DELETE FROM dim_pasillo_coord WHERE cod_tienda=%s", (cod_tienda,))
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO dim_pasillo_coord (cod_tienda, pasillo, x, y) VALUES %s",
            [(cod_tienda, str(r.pasillo), int(r.x), int(r.y)) for r in df_coords.itertuples()],
        )
        cn.commit()


def tiendas_con_plano():
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute("SELECT cod_tienda FROM dim_plano")
        return [r[0] for r in cur.fetchall()]
