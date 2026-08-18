"""Conexión y queries a Postgres para el panel Rentabilidad Rack (Dash)."""
import os
import io
import functools
import pandas as pd
import psycopg2
import psycopg2.extras

_HERE = os.path.dirname(os.path.abspath(__file__))

FILTER_COLS = {
    "familia": "d.familia",
    "categoria": "d.categoria",
    "clasificacion": "d.clasificacion",
    "zona_pck": "d.zona_pck",
    "responsable_linea": "d.responsable_linea",
    "maneja_stock": "d.maneja_stock",
    "marca": "d.marca",
}


def get_conn():
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


def ensure_ready():
    """Crea las tablas que falten y puebla SOLO las que estén vacías con
    los datos empaquetados en seed_*.csv.gz -- seguro de repetir."""
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
            rows)
        conn.commit()
        return len(rows)

    resultados = {}
    resultados["tiendas"] = _upsert_tiendas()
    resultados["venta"] = _cargar(
        "fact_venta_semana",
        ["cod_tienda", "anio", "mes", "semana", "pasillo", "rack", "cod_rapido", "venta", "cantidad"],
        "seed_fact_venta_semana.csv.gz")
    resultados["productos"] = _cargar(
        "dim_producto_tienda",
        ["cod_tienda", "cod_rapido", "descripcion", "marca", "stock", "familia", "subfamilia",
         "categoria", "clasificacion", "maneja_stock", "zona_pck", "responsable_linea"],
        "seed_producto_tienda.csv.gz")
    _cargar("fact_pasillo_rack_anio", ["cod_tienda", "anio", "pasillo", "rack", "venta"],
            "seed_fact_pasillo_rack_anio.csv")
    _cargar("fact_producto_anio", ["cod_tienda", "anio", "cod_rapido", "venta", "cantidad"],
            "seed_fact_producto_anio.csv.gz")
    _cargar("fact_comparativo_anio", ["cod_tienda", "anio", "venta", "trx", "clientes"],
            "seed_comparativo_anio.csv")
    resultados["cross_sell"] = _cargar(
        "fact_cross_sell",
        ["cod_tienda", "sku_a", "sku_b", "desc_a", "desc_b", "boletas", "soporte",
         "confianza_a_b", "confianza_b_a", "lift"],
        "seed_cross_sell.csv.gz")

    plano_path = os.path.join(_HERE, "seed_plano_sanro.png")
    if os.path.exists(plano_path) and _vacia("dim_plano"):
        from PIL import Image
        img = Image.open(plano_path)
        with open(plano_path, "rb") as f:
            cur.execute(
                "INSERT INTO dim_plano (cod_tienda, imagen, img_w, img_h) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (cod_tienda) DO NOTHING",
                ("SANRO", psycopg2.Binary(f.read()), img.width, img.height))
        conn.commit()
    _cargar("dim_pasillo_coord", ["cod_tienda", "pasillo", "x", "y"], "seed_coords_sanro.csv")
    _cargar("dim_rack_coord", ["cod_tienda", "rack", "x", "y"], "seed_rack_coords_sanro.csv")

    conn.commit()
    cur.close()
    conn.close()
    return resultados


def _df(query, params=None):
    with get_conn() as cn:
        return pd.read_sql(query, cn, params=params)


@functools.lru_cache(maxsize=8)
def get_tiendas():
    return _df("SELECT cod_tienda, nombre, tipo FROM dim_tienda ORDER BY cod_tienda")


@functools.lru_cache(maxsize=32)
def get_periodos(cod_tienda):
    meses = _df("SELECT DISTINCT anio, mes FROM fact_venta_semana WHERE cod_tienda=%(t)s ORDER BY anio, mes",
                {"t": cod_tienda})
    semanas = _df("SELECT DISTINCT anio, mes, semana FROM fact_venta_semana WHERE cod_tienda=%(t)s "
                  "ORDER BY anio, semana", {"t": cod_tienda})
    return meses, semanas


@functools.lru_cache(maxsize=32)
def get_opciones_filtro(cod_tienda):
    out = {}
    for key, col in FILTER_COLS.items():
        col_only = col.split(".")[1]
        df = _df(f"SELECT DISTINCT {col_only} AS v FROM dim_producto_tienda "
                 f"WHERE cod_tienda=%(t)s AND {col_only} IS NOT NULL AND {col_only} <> '' ORDER BY 1",
                 {"t": cod_tienda})
        out[key] = df["v"].tolist()
    return out


def _where(cod_tienda, anio, mes, semana, filtros):
    """WHERE de período (sobre f=fact_venta_semana) + filtros de clasificación (sobre d=dim_producto_tienda).
    Se usa siempre con un JOIN f.cod_tienda=d.cod_tienda AND f.cod_rapido=d.cod_rapido."""
    where = ["f.cod_tienda = %(t)s", "f.anio = %(a)s"]
    params = {"t": cod_tienda, "a": anio}
    if semana:
        where.append("f.semana = %(s)s")
        params["s"] = semana
    elif mes:
        where.append("f.mes = %(m)s")
        params["m"] = mes
    for key, col in FILTER_COLS.items():
        vals = (filtros or {}).get(key)
        if vals:
            where.append(f"{col} = ANY(%({key})s)")
            params[key] = list(vals)
    return " AND ".join(where), params


_JOIN = "FROM fact_venta_semana f JOIN dim_producto_tienda d ON d.cod_tienda=f.cod_tienda AND d.cod_rapido=f.cod_rapido"


def get_pasillo_resumen(cod_tienda, anio, mes=None, semana=None, filtros=None):
    where, params = _where(cod_tienda, anio, mes, semana, filtros)
    q = (f"SELECT f.pasillo, SUM(f.venta) AS venta, SUM(f.margen) AS margen, COUNT(DISTINCT f.rack) AS racks "
         f"{_JOIN} WHERE {where} GROUP BY f.pasillo ORDER BY venta DESC")
    return _df(q, params)


def get_rack_detalle(cod_tienda, anio, mes=None, semana=None, filtros=None):
    where, params = _where(cod_tienda, anio, mes, semana, filtros)
    q = (f"SELECT f.pasillo, f.rack, SUM(f.venta) AS venta, SUM(f.margen) AS margen "
         f"{_JOIN} WHERE {where} GROUP BY f.pasillo, f.rack ORDER BY venta DESC")
    return _df(q, params)


def get_venta_por_nivel(cod_tienda, anio, mes=None, semana=None, filtros=None, nivel="pasillo"):
    where, params = _where(cod_tienda, anio, mes, semana, filtros)
    q = (f"SELECT f.{nivel} AS clave, SUM(f.venta) AS venta {_JOIN} WHERE {where} GROUP BY f.{nivel}")
    return _df(q, params)


def get_top_productos(cod_tienda, anio, mes=None, semana=None, filtros=None, pasillo=None, rack=None,
                       n=50, ascendente=False):
    where, params = _where(cod_tienda, anio, mes, semana, filtros)
    if pasillo:
        where += " AND f.pasillo = %(pasillo)s"
        params["pasillo"] = pasillo
    if rack:
        where += " AND f.rack = %(rack)s"
        params["rack"] = rack
    orden = "ASC" if ascendente else "DESC"
    having = "HAVING SUM(f.venta) > 0" if ascendente else ""
    params["n"] = n
    q = (f"SELECT f.cod_rapido, d.descripcion, d.marca, "
         f"CASE d.maneja_stock WHEN 'S' THEN 'Sí' WHEN 'N' THEN 'No' ELSE d.maneja_stock END AS maneja_stock, "
         f"d.stock, SUM(f.venta) AS venta, SUM(f.cantidad) AS cantidad "
         f"{_JOIN} WHERE {where} "
         f"GROUP BY f.cod_rapido, d.descripcion, d.marca, d.maneja_stock, d.stock {having} "
         f"ORDER BY venta {orden} LIMIT %(n)s")
    return _df(q, params)


def get_sin_venta(cod_tienda, anio, mes=None, semana=None, filtros=None, n=300):
    where_f, params = _where(cod_tienda, anio, mes, semana, filtros)
    params["n"] = n
    filtro_extra = ""
    for key, col in FILTER_COLS.items():
        vals = (filtros or {}).get(key)
        if vals:
            filtro_extra += f" AND d2.{col.split('.')[1]} = ANY(%({key})s)"
    q = (f"SELECT d2.cod_rapido, d2.descripcion, d2.marca, d2.stock FROM dim_producto_tienda d2 "
         f"WHERE d2.cod_tienda = %(t)s AND d2.maneja_stock = 'S' {filtro_extra} "
         f"AND NOT EXISTS (SELECT 1 {_JOIN} WHERE {where_f} AND f.cod_rapido = d2.cod_rapido AND f.venta > 0) "
         f"ORDER BY d2.stock DESC NULLS LAST LIMIT %(n)s")
    return _df(q, params)


def get_recomendacion_pasillo(cod_tienda):
    q = ("SELECT pasillo, rack, anio, SUM(venta) AS venta FROM fact_pasillo_rack_anio "
         "WHERE cod_tienda=%(t)s GROUP BY pasillo, rack, anio")
    df = _df(q, {"t": cod_tienda})
    if df.empty:
        return df
    anios = sorted(df["anio"].unique())
    if len(anios) < 2:
        return pd.DataFrame()
    a_actual, a_anterior = anios[-1], anios[-2]
    piv = df.pivot_table(index=["pasillo", "rack"], columns="anio", values="venta", fill_value=0).reset_index()
    piv["venta"] = piv[a_actual]
    piv["venta_anio_anterior"] = piv[a_anterior]
    media_store = piv["venta"].mean() or 1
    piv["variacion_pct"] = ((piv["venta"] - piv["venta_anio_anterior"]) /
                             piv["venta_anio_anterior"].replace(0, pd.NA)) * 100

    def _recom(row):
        crece = pd.notna(row["variacion_pct"]) and row["variacion_pct"] > 10
        cae = pd.notna(row["variacion_pct"]) and row["variacion_pct"] < -10
        sobre_media = row["venta"] > media_store
        if crece and sobre_media:
            return "Aumentar espacio"
        if cae and not sobre_media:
            return "Reducir espacio"
        if cae:
            return "Revisar"
        return "Mantener"

    piv["recomendacion"] = piv.apply(_recom, axis=1)
    return piv[["pasillo", "rack", "venta", "venta_anio_anterior", "variacion_pct", "recomendacion"]] \
        .sort_values("venta", ascending=False)


def get_treemap(cod_tienda, anio, mes=None, semana=None, filtros=None):
    """Familia > Jefe de línea > Categoría."""
    where, params = _where(cod_tienda, anio, mes, semana, filtros)
    q = (f"SELECT COALESCE(d.familia,'(sin familia)') AS familia, "
         f"COALESCE(d.responsable_linea,'(sin jefe de línea)') AS jefe_linea, "
         f"COALESCE(d.categoria,'(sin categoría)') AS categoria, SUM(f.venta) AS venta "
         f"{_JOIN} WHERE {where} AND f.venta > 0 "
         f"GROUP BY d.familia, d.responsable_linea, d.categoria")
    return _df(q, params)


def get_sin_coordenadas(cod_tienda, nivel="pasillo"):
    """Pasillos/racks que tienen venta pero no coordenada en el plano."""
    tabla_coord = "dim_pasillo_coord" if nivel == "pasillo" else "dim_rack_coord"
    col = "pasillo" if nivel == "pasillo" else "rack"
    q = (f"SELECT f.{col} AS clave, SUM(f.venta) AS venta FROM fact_venta_semana f "
         f"WHERE f.cod_tienda=%(t)s AND f.{col} <> '' "
         f"AND NOT EXISTS (SELECT 1 FROM {tabla_coord} c WHERE c.cod_tienda=f.cod_tienda AND c.{col}=f.{col}) "
         f"GROUP BY f.{col} ORDER BY venta DESC")
    return _df(q, {"t": cod_tienda})


def get_comparativo_anio(cod_tienda):
    df = _df("SELECT anio, venta, trx, clientes FROM fact_comparativo_anio WHERE cod_tienda=%(t)s ORDER BY anio",
             {"t": cod_tienda})
    if df.empty:
        return None
    df["ticket_promedio"] = df["venta"] / df["trx"].replace(0, pd.NA)
    return df


def get_top_combos(cod_tienda, n=30, orden="boletas"):
    col = {"boletas": "boletas", "lift": "lift", "confianza": "confianza_a_b"}.get(orden, "boletas")
    return _df(
        f"SELECT desc_a, desc_b, boletas, soporte, confianza_a_b, lift FROM fact_cross_sell "
        f"WHERE cod_tienda=%(t)s ORDER BY {col} DESC LIMIT %(n)s", {"t": cod_tienda, "n": n})


def get_productos_lista(cod_tienda):
    return _df(
        "SELECT DISTINCT sku, descripcion FROM ("
        "  SELECT sku_a AS sku, desc_a AS descripcion FROM fact_cross_sell WHERE cod_tienda=%(t)s "
        "  UNION SELECT sku_b, desc_b FROM fact_cross_sell WHERE cod_tienda=%(t)s"
        ") x ORDER BY descripcion", {"t": cod_tienda})


def get_combos_de_producto(cod_tienda, cod_rapido, n=15):
    return _df(
        "SELECT CASE WHEN sku_a=%(sku)s THEN desc_b ELSE desc_a END AS producto, boletas, "
        "CASE WHEN sku_a=%(sku)s THEN confianza_a_b ELSE confianza_b_a END AS confianza, lift "
        "FROM fact_cross_sell WHERE cod_tienda=%(t)s AND (sku_a=%(sku)s OR sku_b=%(sku)s) "
        "ORDER BY boletas DESC LIMIT %(n)s",
        {"t": cod_tienda, "sku": cod_rapido, "n": n})


def get_plano(cod_tienda):
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute("SELECT imagen, img_w, img_h FROM dim_plano WHERE cod_tienda=%s", (cod_tienda,))
        row = cur.fetchone()
    if not row:
        return None
    imagen, w, h = row
    return {"imagen": bytes(imagen), "img_w": w, "img_h": h}


def get_coords(cod_tienda, nivel="pasillo"):
    tabla = "dim_pasillo_coord" if nivel == "pasillo" else "dim_rack_coord"
    col = "pasillo" if nivel == "pasillo" else "rack"
    return _df(f"SELECT {col} AS clave, x, y FROM {tabla} WHERE cod_tienda=%(t)s", {"t": cod_tienda})


def guardar_plano(cod_tienda, imagen_bytes, img_w, img_h):
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute(
            "INSERT INTO dim_plano (cod_tienda, imagen, img_w, img_h, actualizado) VALUES (%s,%s,%s,%s,now()) "
            "ON CONFLICT (cod_tienda) DO UPDATE SET imagen=EXCLUDED.imagen, img_w=EXCLUDED.img_w, "
            "img_h=EXCLUDED.img_h, actualizado=now()", (cod_tienda, psycopg2.Binary(imagen_bytes), img_w, img_h))
        cn.commit()


def guardar_coords(cod_tienda, df_coords, nivel="pasillo"):
    df_coords = df_coords.dropna(subset=["x", "y"])
    tabla = "dim_pasillo_coord" if nivel == "pasillo" else "dim_rack_coord"
    col = "pasillo" if nivel == "pasillo" else "rack"
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute(f"DELETE FROM {tabla} WHERE cod_tienda=%s", (cod_tienda,))
        psycopg2.extras.execute_values(
            cur, f"INSERT INTO {tabla} (cod_tienda, {col}, x, y) VALUES %s",
            [(cod_tienda, str(getattr(r, col)), int(r.x), int(r.y)) for r in df_coords.itertuples()])
        cn.commit()


def tiendas_con_plano():
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute("SELECT cod_tienda FROM dim_plano")
        return [r[0] for r in cur.fetchall()]
