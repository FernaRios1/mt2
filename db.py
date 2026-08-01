"""Conexión y queries a Postgres para el panel Rentabilidad Rack."""
import os
import io
import pandas as pd
import psycopg2
import psycopg2.extras
import streamlit as st

_HERE = os.path.dirname(os.path.abspath(__file__))

FILTER_COLS = {
    "familia": "familia",
    "categoria": "categoria",
    "clasificacion": "clasificacion",
    "zona_pck": "zona_pck",
    "responsable_linea": "responsable_linea",
    "maneja_stock": "maneja_stock",
}


def get_conn():
    """Conexión a Postgres. En Railway, DATABASE_URL viene inyectada sola
    si agregas el plugin de Postgres al mismo proyecto."""
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        dsn = dsn.replace("postgres://", "postgresql://", 1)
        return psycopg2.connect(dsn)
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "rentabilidad"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", ""),
    )


@st.cache_resource
def ensure_ready():
    """Crea las tablas que falten (CREATE TABLE IF NOT EXISTS, siempre seguro
    de repetir) y puebla con los datos empaquetados en seed_*.csv.gz
    SOLO las tablas que todavía estén vacías -- así funciona tanto en una
    base nueva como en una que ya tenía algo de un despliegue anterior."""
    conn = get_conn()
    cur = conn.cursor()

    with open(os.path.join(_HERE, "schema.sql"), encoding="utf-8") as f:
        cur.execute(f.read())
    conn.commit()

    def _vacia(tabla):
        cur.execute(f"SELECT NOT EXISTS (SELECT 1 FROM {tabla} LIMIT 1)")
        return cur.fetchone()[0]

    def _cargar(tabla, cols, archivo):
        if not _vacia(tabla):
            return 0
        path = os.path.join(_HERE, archivo)
        if not os.path.exists(path):
            return 0
        df = pd.read_csv(path)[cols]
        if df.empty:
            return 0
        for c in ("x", "y"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce").round().astype("Int64")
        buf = io.StringIO()
        df.to_csv(buf, index=False, header=False)
        buf.seek(0)
        cur.copy_expert(f"COPY {tabla} ({','.join(cols)}) FROM STDIN WITH (FORMAT csv, NULL '')", buf)
        conn.commit()
        return len(df)

    def _upsert_tiendas():
        path = os.path.join(_HERE, "seed_tiendas.csv")
        if not os.path.exists(path):
            return 0
        df = pd.read_csv(path)
        rows = list(df[["cod_tienda", "nombre", "tipo"]].itertuples(index=False, name=None))
        psycopg2.extras.execute_values(
            cur, "INSERT INTO dim_tienda (cod_tienda,nombre,tipo) VALUES %s ON CONFLICT (cod_tienda) DO NOTHING",
            rows,
        )
        conn.commit()
        return len(rows)

    n1 = _upsert_tiendas()
    n2 = _cargar(
        "fact_venta_semana",
        ["cod_tienda", "anio", "mes", "semana", "pasillo", "rack", "cod_rapido", "descripcion",
         "familia", "subfamilia", "categoria", "clasificacion", "maneja_stock", "zona_pck",
         "responsable_linea", "venta", "cantidad"],
        "seed_fact_venta_semana.csv.gz",
    )
    n3 = _cargar("dim_producto_tienda",
                 ["cod_tienda", "cod_rapido", "descripcion", "maneja_stock"],
                 "seed_producto_tienda.csv.gz")

    plano_path = os.path.join(_HERE, "seed_plano_sanro.png")
    coords_path = os.path.join(_HERE, "seed_coords_sanro.csv")
    rack_coords_path = os.path.join(_HERE, "seed_rack_coords_sanro.csv")
    if os.path.exists(plano_path) and _vacia("dim_plano"):
        from PIL import Image
        img = Image.open(plano_path)
        with open(plano_path, "rb") as f:
            cur.execute(
                "INSERT INTO dim_plano (cod_tienda, imagen, img_w, img_h) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (cod_tienda) DO NOTHING",
                ("SANRO", psycopg2.Binary(f.read()), img.width, img.height),
            )
        conn.commit()
    if os.path.exists(coords_path):
        _cargar("dim_pasillo_coord", ["cod_tienda", "pasillo", "x", "y"], "seed_coords_sanro.csv")
    if os.path.exists(rack_coords_path):
        _cargar("dim_rack_coord", ["cod_tienda", "rack", "x", "y"], "seed_rack_coords_sanro.csv")

    conn.commit()
    cur.close()
    conn.close()
    return f"base inicializada: {n1} tiendas, {n2} filas de venta, {n3} SKU"


def _df(query, params=None):
    with get_conn() as cn:
        return pd.read_sql(query, cn, params=params)


@st.cache_data(ttl=300)
def get_tiendas():
    return _df("SELECT cod_tienda, nombre, tipo FROM dim_tienda ORDER BY cod_tienda")


@st.cache_data(ttl=300)
def get_periodos(cod_tienda):
    meses = _df("SELECT DISTINCT anio, mes FROM fact_venta_semana WHERE cod_tienda=%(t)s ORDER BY anio, mes",
                {"t": cod_tienda})
    semanas = _df("SELECT DISTINCT anio, mes, semana FROM fact_venta_semana WHERE cod_tienda=%(t)s ORDER BY anio, semana",
                  {"t": cod_tienda})
    return meses, semanas


@st.cache_data(ttl=300)
def get_opciones_filtro(cod_tienda):
    """Valores disponibles para poblar los selectores de filtro."""
    out = {}
    for key, col in FILTER_COLS.items():
        df = _df(f"SELECT DISTINCT {col} AS v FROM fact_venta_semana "
                 f"WHERE cod_tienda=%(t)s AND {col} IS NOT NULL AND {col} <> '' ORDER BY 1",
                 {"t": cod_tienda})
        out[key] = df["v"].tolist()
    return out


def _where(cod_tienda, anio, mes, semana, filtros, alias=None):
    """Arma el WHERE de período + filtros de clasificación. filtros: dict
    {familia: [...], categoria: [...], clasificacion: [...], zona_pck: [...],
     responsable_linea: [...], maneja_stock: [...]} -- listas vacías u
    omitidas no filtran esa dimensión. alias: prefijo de tabla opcional
    (ej. 'f') para usar en subconsultas con JOIN/EXISTS."""
    p = f"{alias}." if alias else ""
    where = [f"{p}cod_tienda = %(t)s", f"{p}anio = %(a)s"]
    params = {"t": cod_tienda, "a": anio}
    if semana is not None:
        where.append(f"{p}semana = %(s)s")
        params["s"] = semana
    elif mes is not None:
        where.append(f"{p}mes = %(m)s")
        params["m"] = mes
    for key, col in FILTER_COLS.items():
        vals = (filtros or {}).get(key)
        if vals:
            where.append(f"{p}{col} = ANY(%({key})s)")
            params[key] = list(vals)
    return " AND ".join(where), params


@st.cache_data(ttl=300)
def get_pasillo_resumen(cod_tienda, anio, mes=None, semana=None, filtros=None):
    where, params = _where(cod_tienda, anio, mes, semana, filtros)
    q = (f"SELECT pasillo, SUM(venta) AS venta, SUM(margen) AS margen, COUNT(DISTINCT rack) AS racks "
         f"FROM fact_venta_semana WHERE {where} GROUP BY pasillo ORDER BY venta DESC")
    return _df(q, params)


@st.cache_data(ttl=300)
def get_rack_detalle(cod_tienda, anio, mes=None, semana=None, filtros=None):
    where, params = _where(cod_tienda, anio, mes, semana, filtros)
    q = (f"SELECT pasillo, rack, SUM(venta) AS venta, SUM(margen) AS margen "
         f"FROM fact_venta_semana WHERE {where} GROUP BY pasillo, rack ORDER BY venta DESC")
    return _df(q, params)


@st.cache_data(ttl=300)
def get_venta_por_nivel(cod_tienda, anio, mes=None, semana=None, filtros=None, nivel="pasillo"):
    """nivel: 'pasillo' o 'rack' -- para el mapa de calor."""
    where, params = _where(cod_tienda, anio, mes, semana, filtros)
    q = (f"SELECT {nivel} AS clave, SUM(venta) AS venta FROM fact_venta_semana "
         f"WHERE {where} GROUP BY {nivel}")
    return _df(q, params)


@st.cache_data(ttl=300)
def get_top_productos(cod_tienda, anio, mes=None, semana=None, filtros=None, n=50, ascendente=False):
    where, params = _where(cod_tienda, anio, mes, semana, filtros)
    orden = "ASC" if ascendente else "DESC"
    having = "HAVING SUM(venta) > 0" if ascendente else ""
    params["n"] = n
    q = (f"SELECT cod_rapido, descripcion, SUM(venta) AS venta, SUM(cantidad) AS cantidad "
         f"FROM fact_venta_semana WHERE {where} "
         f"GROUP BY cod_rapido, descripcion {having} ORDER BY venta {orden} LIMIT %(n)s")
    return _df(q, params)


@st.cache_data(ttl=300)
def get_sin_venta(cod_tienda, anio, mes=None, semana=None, filtros=None, n=300):
    """Productos con stock que no aparecen con venta > 0 en el período/filtros elegidos."""
    where_f, params = _where(cod_tienda, anio, mes, semana, filtros, alias="f")
    params["n"] = n
    q = (f"SELECT d.cod_rapido, d.descripcion FROM dim_producto_tienda d "
         f"WHERE d.cod_tienda = %(t)s AND d.maneja_stock = 'S' "
         f"AND NOT EXISTS (SELECT 1 FROM fact_venta_semana f WHERE {where_f} "
         f"AND f.cod_rapido = d.cod_rapido AND f.venta > 0) "
         f"ORDER BY d.descripcion LIMIT %(n)s")
    return _df(q, params)


@st.cache_data(ttl=600)
def get_plano(cod_tienda):
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute("SELECT imagen, img_w, img_h FROM dim_plano WHERE cod_tienda=%s", (cod_tienda,))
        row = cur.fetchone()
    if not row:
        return None
    imagen, w, h = row
    return {"imagen": bytes(imagen), "img_w": w, "img_h": h}


@st.cache_data(ttl=600)
def get_coords(cod_tienda, nivel="pasillo"):
    tabla = "dim_pasillo_coord" if nivel == "pasillo" else "dim_rack_coord"
    col = "pasillo" if nivel == "pasillo" else "rack"
    return _df(f"SELECT {col} AS clave, x, y FROM {tabla} WHERE cod_tienda=%(t)s", {"t": cod_tienda})


def guardar_plano(cod_tienda, imagen_bytes, img_w, img_h):
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute(
            "INSERT INTO dim_plano (cod_tienda, imagen, img_w, img_h, actualizado) VALUES (%s, %s, %s, %s, now()) "
            "ON CONFLICT (cod_tienda) DO UPDATE SET imagen = EXCLUDED.imagen, img_w = EXCLUDED.img_w, "
            "img_h = EXCLUDED.img_h, actualizado = now()",
            (cod_tienda, psycopg2.Binary(imagen_bytes), img_w, img_h),
        )
        cn.commit()


def guardar_coords(cod_tienda, df_coords, nivel="pasillo"):
    """df_coords: columnas pasillo|rack, x, y. Filas sin x/y numérico se descartan."""
    df_coords = df_coords.dropna(subset=["x", "y"])
    tabla = "dim_pasillo_coord" if nivel == "pasillo" else "dim_rack_coord"
    col = "pasillo" if nivel == "pasillo" else "rack"
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute(f"DELETE FROM {tabla} WHERE cod_tienda=%s", (cod_tienda,))
        psycopg2.extras.execute_values(
            cur, f"INSERT INTO {tabla} (cod_tienda, {col}, x, y) VALUES %s",
            [(cod_tienda, str(getattr(r, col)), int(r.x), int(r.y)) for r in df_coords.itertuples()],
        )
        cn.commit()


def tiendas_con_plano():
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute("SELECT cod_tienda FROM dim_plano")
        return [r[0] for r in cur.fetchall()]
