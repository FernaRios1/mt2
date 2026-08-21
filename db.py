"""Conexión y queries a Postgres para el panel Rentabilidad Rack (Dash)."""
import os
import io
import functools
import datetime
import calendar
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


def get_periodos(cod_tienda):
    meses = _df("SELECT DISTINCT anio, mes FROM fact_venta_semana WHERE cod_tienda=%(t)s ORDER BY anio, mes",
                {"t": cod_tienda})
    semanas = _df("SELECT DISTINCT anio, mes, semana FROM fact_venta_semana WHERE cod_tienda=%(t)s "
                  "ORDER BY anio, semana", {"t": cod_tienda})
    return meses, semanas


def get_opciones_filtro(cod_tienda):
    out = {}
    for key, col in FILTER_COLS.items():
        col_only = col.split(".")[1]
        df = _df(f"SELECT DISTINCT {col_only} AS v FROM dim_producto_tienda "
                 f"WHERE cod_tienda=%(t)s AND {col_only} IS NOT NULL AND {col_only} <> '' ORDER BY 1",
                 {"t": cod_tienda})
        out[key] = df["v"].tolist()
    return out


def get_opciones_filtro_dependientes(cod_tienda, filtros=None):
    """Opciones de filtros en cascada.

    Cada selector se calcula aplicando los demás filtros activos, pero no su
    propio valor. Así, por ejemplo, al elegir una familia la lista de categorías
    muestra solo categorías realmente existentes dentro de esa familia.
    """
    out = {}
    for target, target_col in FILTER_COLS.items():
        target_only = target_col.split(".")[1]
        where = ["d.cod_tienda=%(t)s", f"d.{target_only} IS NOT NULL", f"d.{target_only} <> ''"]
        params = {"t": cod_tienda}
        for key, col in FILTER_COLS.items():
            if key == target:
                continue
            vals = (filtros or {}).get(key)
            if vals:
                where.append(f"{col} = ANY(%({key})s)")
                params[key] = list(vals)
        q = (f"SELECT DISTINCT d.{target_only} AS v FROM dim_producto_tienda d "
             f"WHERE {' AND '.join(where)} ORDER BY 1")
        df = _df(q, params)
        out[target] = df["v"].tolist()
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


def get_anio_actual(cod_tienda):
    """Último año disponible para la tienda. Evita dejar el año fijo en el código."""
    df = _df("SELECT MAX(anio) AS anio FROM fact_venta_semana WHERE cod_tienda=%(t)s", {"t": cod_tienda})
    if df.empty or pd.isna(df.iloc[0]["anio"]):
        return None
    return int(df.iloc[0]["anio"])


def _agregar_seccion(where, params, pasillo=None, rack=None):
    if pasillo:
        where += " AND f.pasillo = %(pasillo)s"
        params["pasillo"] = pasillo
    if rack:
        where += " AND f.rack = %(rack)s"
        params["rack"] = rack
    return where, params


def get_resumen_periodo(cod_tienda, anio, mes=None, semana=None, filtros=None, pasillo=None, rack=None):
    """KPIs del período y comparación equivalente.

    Prioriza el mismo mes/semana/año del año anterior con exactamente los
    mismos filtros y la misma sección. Si todavía no existe detalle del año
    anterior, Mes/Semana pueden caer al período previo del mismo año. Para Año,
    el comparativo agregado de tienda solo se usa cuando no hay filtros ni una
    sección seleccionada, para no mezclar universos distintos.
    """
    where, params = _where(cod_tienda, anio, mes, semana, filtros)
    where, params = _agregar_seccion(where, params, pasillo, rack)
    q = (f"SELECT COALESCE(SUM(f.venta),0) AS venta, COALESCE(SUM(f.margen),0) AS margen, "
         f"COALESCE(SUM(f.cantidad),0) AS unidades, COUNT(DISTINCT f.pasillo) AS pasillos, "
         f"COUNT(DISTINCT f.rack) AS racks, COUNT(DISTINCT f.cod_rapido) AS skus "
         f"{_JOIN} WHERE {where}")
    cur = _df(q, params).iloc[0].to_dict()

    prev_label = None
    prev_venta = None

    # Mismo período del año anterior. COUNT(*) distingue "sin filas" de una
    # venta neta igual a cero, que también puede ser un resultado válido.
    wp, pp = _where(cod_tienda, anio - 1, mes, semana, filtros)
    wp, pp = _agregar_seccion(wp, pp, pasillo, rack)
    prev_same = _df(
        f"SELECT COUNT(*) AS filas, COALESCE(SUM(f.venta),0) AS venta {_JOIN} WHERE {wp}", pp
    ).iloc[0]
    if int(prev_same["filas"] or 0) > 0:
        prev_venta = float(prev_same["venta"] or 0)
        if semana:
            prev_label = f"Semana {semana}/{anio - 1}"
        elif mes:
            prev_label = f"{mes}/{anio - 1}"
        else:
            prev_label = str(anio - 1)
    elif semana:
        d = _df("SELECT MAX(semana) AS p FROM fact_venta_semana "
                "WHERE cod_tienda=%(t)s AND anio=%(a)s AND semana < %(s)s",
                {"t": cod_tienda, "a": anio, "s": semana})
        p = d.iloc[0]["p"] if len(d) else None
        if pd.notna(p):
            p = int(p)
            wp, pp = _where(cod_tienda, anio, None, p, filtros)
            wp, pp = _agregar_seccion(wp, pp, pasillo, rack)
            prev = _df(f"SELECT COUNT(*) AS filas, COALESCE(SUM(f.venta),0) AS venta {_JOIN} WHERE {wp}", pp).iloc[0]
            if int(prev["filas"] or 0) > 0:
                prev_venta = float(prev["venta"] or 0)
                prev_label = f"Semana {p}"
    elif mes:
        d = _df("SELECT MAX(mes) AS p FROM fact_venta_semana "
                "WHERE cod_tienda=%(t)s AND anio=%(a)s AND mes < %(m)s",
                {"t": cod_tienda, "a": anio, "m": mes})
        p = d.iloc[0]["p"] if len(d) else None
        if pd.notna(p):
            p = int(p)
            wp, pp = _where(cod_tienda, anio, p, None, filtros)
            wp, pp = _agregar_seccion(wp, pp, pasillo, rack)
            prev = _df(f"SELECT COUNT(*) AS filas, COALESCE(SUM(f.venta),0) AS venta {_JOIN} WHERE {wp}", pp).iloc[0]
            if int(prev["filas"] or 0) > 0:
                prev_venta = float(prev["venta"] or 0)
                prev_label = f"Mes {p}"
    else:
        filtros_activos = any((filtros or {}).get(k) for k in FILTER_COLS)
        if not filtros_activos and not pasillo and not rack:
            comp = get_comparativo_anio(cod_tienda)
            if comp is not None and len(comp) >= 2:
                prev = comp[comp["anio"] < anio].tail(1)
                if len(prev):
                    prev_venta = float(prev.iloc[0]["venta"])
                    prev_label = str(int(prev.iloc[0]["anio"]))

    cur["venta_anterior"] = prev_venta
    cur["periodo_anterior"] = prev_label
    cur["variacion_pct"] = ((float(cur["venta"]) - prev_venta) / prev_venta * 100) if prev_venta not in (None, 0) else None
    return cur


def get_sin_venta_count(cod_tienda, anio, filtros=None, pasillo=None, rack=None):
    """Cantidad real de SKU con stock positivo y sin venta en el año.

    Si el agente V3 ya pobló pasillo/rack en dim_producto_tienda, también puede
    limitarse a la sección seleccionada aunque el SKU no haya vendido.
    """
    filtro_extra = ""
    params = {"t": cod_tienda, "a": anio}
    for key, col in FILTER_COLS.items():
        vals = (filtros or {}).get(key)
        if vals:
            filtro_extra += f" AND d.{col.split('.')[1]} = ANY(%({key})s)"
            params[key] = list(vals)
    if pasillo:
        filtro_extra += " AND d.pasillo = %(pasillo)s"
        params["pasillo"] = pasillo
    if rack:
        filtro_extra += " AND d.rack = %(rack)s"
        params["rack"] = rack
    q = f"""
    SELECT COUNT(*) AS n
    FROM dim_producto_tienda d
    WHERE d.cod_tienda=%(t)s AND d.maneja_stock='S' AND COALESCE(d.stock,0) > 0 {filtro_extra}
      AND NOT EXISTS (
          SELECT 1 FROM fact_venta_semana f
          WHERE f.cod_tienda=d.cod_tienda AND f.cod_rapido=d.cod_rapido
            AND f.anio=%(a)s AND f.venta > 0
      )
    """
    df = _df(q, params)
    return int(df.iloc[0]["n"]) if len(df) else 0

def get_tendencia_semana(cod_tienda, anio, filtros=None, pasillo=None, rack=None):
    """Serie semanal del año para dar contexto al período seleccionado."""
    where, params = _where(cod_tienda, anio, None, None, filtros)
    where, params = _agregar_seccion(where, params, pasillo, rack)
    q = (f"SELECT f.semana, SUM(f.venta) AS venta {_JOIN} WHERE {where} "
         f"GROUP BY f.semana ORDER BY f.semana")
    return _df(q, params)


def get_acciones_rack(cod_tienda, anio, mes=None, semana=None, filtros=None):
    """Motor explicable de recomendaciones por rack para el período seleccionado.

    Prioriza una comparación contra el mismo período del año anterior con los
    mismos filtros. Si ese detalle todavía no existe, Mes/Semana usan el período
    previo del mismo año como respaldo. El motor no usa margen mientras el origen
    siga entregándolo en cero.
    """
    where, params = _where(cod_tienda, anio, mes, semana, filtros)
    q = (f"SELECT f.pasillo, f.rack, SUM(f.venta) AS venta, SUM(f.cantidad) AS unidades, "
         f"COUNT(DISTINCT f.cod_rapido) AS skus {_JOIN} WHERE {where} "
         f"GROUP BY f.pasillo, f.rack")
    df = _df(q, params)
    if df.empty:
        return df

    df["venta"] = pd.to_numeric(df["venta"], errors="coerce").fillna(0.0)
    df["unidades"] = pd.to_numeric(df["unidades"], errors="coerce").fillna(0.0)
    df["skus"] = pd.to_numeric(df["skus"], errors="coerce").fillna(0).astype(int)
    df["venta_por_sku"] = df["venta"] / df["skus"].replace(0, pd.NA)
    total = float(df["venta"].sum()) or 1.0
    df["participacion_pct"] = df["venta"] / total * 100
    df["percentil_venta"] = df["venta"].rank(pct=True, method="average") * 100

    ant = pd.DataFrame(columns=["pasillo", "rack", "venta_anterior"])
    comparacion_label = None

    wp, pp = _where(cod_tienda, anio - 1, mes, semana, filtros)
    ant_same = _df(
        f"SELECT f.pasillo, f.rack, SUM(f.venta) AS venta_anterior, COUNT(*) AS filas "
        f"{_JOIN} WHERE {wp} GROUP BY f.pasillo, f.rack", pp
    )
    if not ant_same.empty:
        ant = ant_same.drop(columns="filas", errors="ignore")
        comparacion_label = (f"Semana {semana}/{anio - 1}" if semana else
                             f"Mes {mes}/{anio - 1}" if mes else str(anio - 1))
    elif semana:
        d = _df("SELECT MAX(semana) AS p FROM fact_venta_semana "
                "WHERE cod_tienda=%(t)s AND anio=%(a)s AND semana < %(s)s",
                {"t": cod_tienda, "a": anio, "s": semana})
        prev = d.iloc[0]["p"] if len(d) else None
        if pd.notna(prev):
            prev = int(prev)
            wp, pp = _where(cod_tienda, anio, None, prev, filtros)
            ant = _df(f"SELECT f.pasillo, f.rack, SUM(f.venta) AS venta_anterior "
                      f"{_JOIN} WHERE {wp} GROUP BY f.pasillo, f.rack", pp)
            if not ant.empty:
                comparacion_label = f"Semana {prev}"
    elif mes:
        d = _df("SELECT MAX(mes) AS p FROM fact_venta_semana "
                "WHERE cod_tienda=%(t)s AND anio=%(a)s AND mes < %(m)s",
                {"t": cod_tienda, "a": anio, "m": mes})
        prev = d.iloc[0]["p"] if len(d) else None
        if pd.notna(prev):
            prev = int(prev)
            wp, pp = _where(cod_tienda, anio, prev, None, filtros)
            ant = _df(f"SELECT f.pasillo, f.rack, SUM(f.venta) AS venta_anterior "
                      f"{_JOIN} WHERE {wp} GROUP BY f.pasillo, f.rack", pp)
            if not ant.empty:
                comparacion_label = f"Mes {prev}"
    else:
        filtros_activos = any((filtros or {}).get(k) for k in FILTER_COLS)
        if not filtros_activos:
            ant = _df("SELECT pasillo, rack, venta AS venta_anterior FROM fact_pasillo_rack_anio "
                      "WHERE cod_tienda=%(t)s AND anio=%(a)s",
                      {"t": cod_tienda, "a": anio - 1})
            if not ant.empty:
                comparacion_label = str(anio - 1)

    if not ant.empty:
        df = df.merge(ant, on=["pasillo", "rack"], how="left")
    else:
        df["venta_anterior"] = pd.NA

    df["venta_anterior"] = pd.to_numeric(df["venta_anterior"], errors="coerce")
    df["variacion_pct"] = ((df["venta"] - df["venta_anterior"]) /
                            df["venta_anterior"].replace(0, pd.NA)) * 100
    df["brecha_venta"] = df["venta"] - df["venta_anterior"]
    df["comparacion_label"] = comparacion_label

    p30 = float(df["venta"].quantile(0.30))
    p50 = float(df["venta"].quantile(0.50))
    p70 = float(df["venta"].quantile(0.70))
    sku70 = float(df["skus"].quantile(0.70))
    eff70 = float(df["venta_por_sku"].dropna().quantile(0.70)) if df["venta_por_sku"].notna().any() else 0

    def _clasificar(row):
        var = row["variacion_pct"]
        tiene_comp = pd.notna(var)
        if row["venta"] >= p70 and tiene_comp and var <= -10:
            return ("Alta", "Proteger venta",
                    f"Rack de alta venta (top 30%) pero cae {abs(var):.1f}% vs período comparable.",
                    "Revisar stock, precio, exhibición y mix antes de perder más venta.")
        if row["venta"] <= p30 and tiene_comp and var <= -10:
            return ("Alta", "Revisar rack",
                    f"Venta baja y caída de {abs(var):.1f}% vs período comparable.",
                    "Validar si el surtido y el espacio se justifican; corregir causa puntual o reducir complejidad.")
        if row["venta"] >= p70 and ((tiene_comp and var >= 10) or
                                    (not tiene_comp and row["venta_por_sku"] >= eff70)):
            extra = f" y crece {var:.1f}%" if tiene_comp else " y tiene alta venta por SKU"
            return ("Media", "Potenciar rack",
                    f"Rack de alta venta{extra}.",
                    "Asegurar stock de líderes y evaluar más caras/exhibición donde físicamente sea viable.")
        if row["venta"] <= p50 and row["skus"] >= sku70:
            return ("Media", "Optimizar surtido",
                    f"{int(row['skus'])} SKU con venta bajo la mediana de la tienda.",
                    "Concentrar exhibición en SKU que sí rotan y revisar duplicidad/variedad de baja venta.")
        return ("Baja", "Mantener",
                "Desempeño sin una señal fuerte de riesgo u oportunidad.",
                "Sin acción urgente; monitorear tendencia y disponibilidad.")

    clas = df.apply(_clasificar, axis=1, result_type="expand")
    clas.columns = ["prioridad", "accion", "motivo", "recomendacion"]
    df = pd.concat([df, clas], axis=1)

    def _score(row):
        var = float(row["variacion_pct"]) if pd.notna(row["variacion_pct"]) else 0.0
        if row["accion"] == "Proteger venta":
            return 100 + min(abs(var), 100) + row["percentil_venta"] / 10
        if row["accion"] == "Revisar rack":
            return 90 + min(abs(var), 100) + (100 - row["percentil_venta"]) / 10
        if row["accion"] == "Potenciar rack":
            return 70 + row["percentil_venta"] / 10 + max(var, 0) / 10
        if row["accion"] == "Optimizar surtido":
            return 60 + min(row["skus"], 100) / 10 + (100 - row["percentil_venta"]) / 20
        return 10 + row["percentil_venta"] / 100

    df["score_orden"] = df.apply(_score, axis=1)
    return df.sort_values(["score_orden", "venta"], ascending=[False, False]).reset_index(drop=True)


def get_sync_status():
    """Última corrida del agente; devuelve None si todavía no existe log."""
    try:
        df = _df("SELECT ejecutado_en, filas_venta, ok, mensaje FROM sync_log ORDER BY ejecutado_en DESC LIMIT 1")
    except Exception:
        return None
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def get_pasillo_resumen(cod_tienda, anio, mes=None, semana=None, filtros=None, pasillo=None, rack=None):
    where, params = _where(cod_tienda, anio, mes, semana, filtros)
    if pasillo:
        where += " AND f.pasillo = %(pasillo)s"
        params["pasillo"] = pasillo
    if rack:
        where += " AND f.rack = %(rack)s"
        params["rack"] = rack
    q = (f"SELECT f.pasillo, SUM(f.venta) AS venta, SUM(f.margen) AS margen, SUM(f.cantidad) AS unidades, "
         f"COUNT(DISTINCT f.rack) AS racks, COUNT(DISTINCT f.cod_rapido) AS skus "
         f"{_JOIN} WHERE {where} GROUP BY f.pasillo ORDER BY venta DESC")
    return _df(q, params)


def get_rack_detalle(cod_tienda, anio, mes=None, semana=None, filtros=None, pasillo=None, rack=None):
    where, params = _where(cod_tienda, anio, mes, semana, filtros)
    if pasillo:
        where += " AND f.pasillo = %(pasillo)s"
        params["pasillo"] = pasillo
    if rack:
        where += " AND f.rack = %(rack)s"
        params["rack"] = rack
    q = (f"SELECT f.pasillo, f.rack, SUM(f.venta) AS venta, SUM(f.margen) AS margen, SUM(f.cantidad) AS unidades, "
         f"COUNT(DISTINCT f.cod_rapido) AS skus "
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
    q = (f"SELECT f.cod_rapido, d.descripcion, d.marca, d.familia, d.categoria, d.responsable_linea, "
         f"CASE d.maneja_stock WHEN 'S' THEN 'Sí' WHEN 'N' THEN 'No' ELSE d.maneja_stock END AS maneja_stock, "
         f"d.stock, SUM(f.venta) AS venta, SUM(f.cantidad) AS cantidad "
         f"{_JOIN} WHERE {where} "
         f"GROUP BY f.cod_rapido, d.descripcion, d.marca, d.familia, d.categoria, d.responsable_linea, "
         f"d.maneja_stock, d.stock {having} "
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


def get_recomendacion_pasillo(cod_tienda, pasillo=None, rack=None):
    q = ("SELECT pasillo, rack, anio, SUM(venta) AS venta FROM fact_pasillo_rack_anio "
         "WHERE cod_tienda=%(t)s GROUP BY pasillo, rack, anio")
    df = _df(q, {"t": cod_tienda})
    if df.empty:
        return df
    if pasillo:
        df = df[df["pasillo"] == pasillo]
    if rack:
        df = df[df["rack"] == rack]
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


def get_treemap(cod_tienda, anio, mes=None, semana=None, filtros=None, pasillo=None, rack=None):
    """Familia > Jefe de línea > Categoría."""
    where, params = _where(cod_tienda, anio, mes, semana, filtros)
    if pasillo:
        where += " AND f.pasillo = %(pasillo)s"
        params["pasillo"] = pasillo
    if rack:
        where += " AND f.rack = %(rack)s"
        params["rack"] = rack
    q = (f"SELECT COALESCE(d.familia,'(sin familia)') AS familia, "
         f"COALESCE(d.responsable_linea,'(sin jefe de línea)') AS jefe_linea, "
         f"COALESCE(d.categoria,'(sin categoría)') AS categoria, SUM(f.venta) AS venta "
         f"{_JOIN} WHERE {where} AND f.venta > 0 "
         f"GROUP BY d.familia, d.responsable_linea, d.categoria")
    return _df(q, params)



def get_surtido_seccion(cod_tienda, filtros=None, pasillo=None, rack=None):
    """Resumen del surtido vigente de la sección, independiente de que haya vendido.

    Usa la ubicación ACTUAL de dim_producto_tienda. Sirve para diferenciar
    "SKU con venta" (hecho del período) de "SKU asociados hoy" (catálogo actual).
    """
    where = ["d.cod_tienda=%(t)s"]
    params = {"t": cod_tienda}
    for key, col in FILTER_COLS.items():
        vals = (filtros or {}).get(key)
        if vals:
            where.append(f"{col} = ANY(%({key})s)")
            params[key] = list(vals)
    if pasillo:
        where.append("d.pasillo=%(pasillo)s")
        params["pasillo"] = pasillo
    if rack:
        where.append("d.rack=%(rack)s")
        params["rack"] = rack
    q = ("SELECT COUNT(DISTINCT d.cod_rapido) AS skus_asociados, "
         "COUNT(DISTINCT CASE WHEN COALESCE(d.stock,0) > 0 THEN d.cod_rapido END) AS skus_con_stock "
         f"FROM dim_producto_tienda d WHERE {' AND '.join(where)}")
    df = _df(q, params)
    if df.empty:
        return {"skus_asociados": 0, "skus_con_stock": 0}
    return {
        "skus_asociados": int(df.iloc[0]["skus_asociados"] or 0),
        "skus_con_stock": int(df.iloc[0]["skus_con_stock"] or 0),
    }


def get_categorias_seccion(cod_tienda, anio, mes=None, semana=None, filtros=None,
                           pasillo=None, rack=None, n=8):
    """Categorías asociadas a la sección seleccionada.

    Con el agente V3 usa la ubicación vigente de ``dim_producto_tienda`` para
    incluir también SKU asociados al rack/pasillo que no vendieron en el
    período. Si la dimensión todavía no tiene ubicación (antes del primer sync
    V3), cae al detalle de venta como respaldo.
    """
    if not pasillo and not rack:
        return pd.DataFrame()

    d_where = ["d.cod_tienda=%(t)s"]
    params = {"t": cod_tienda, "a": anio}
    for key, col in FILTER_COLS.items():
        vals = (filtros or {}).get(key)
        if vals:
            d_where.append(f"{col} = ANY(%({key})s)")
            params[key] = list(vals)
    if pasillo:
        d_where.append("d.pasillo=%(pasillo)s")
        params["pasillo"] = pasillo
    if rack:
        d_where.append("d.rack=%(rack)s")
        params["rack"] = rack

    catalogo = _df(
        "SELECT d.cod_rapido, COALESCE(d.familia,'(sin familia)') AS familia, "
        "COALESCE(d.categoria,'(sin categoría)') AS categoria, "
        "COALESCE(d.responsable_linea,'(sin jefe de línea)') AS jefe_linea, d.stock "
        f"FROM dim_producto_tienda d WHERE {' AND '.join(d_where)}", params
    )

    if not catalogo.empty:
        f_where = ["f.cod_tienda=%(t)s", "f.anio=%(a)s"]
        if semana:
            f_where.append("f.semana=%(semana)s")
            params["semana"] = semana
        elif mes:
            f_where.append("f.mes=%(mes)s")
            params["mes"] = mes
        ventas = _df(
            "SELECT f.cod_rapido, SUM(f.venta) AS venta FROM fact_venta_semana f "
            f"WHERE {' AND '.join(f_where)} GROUP BY f.cod_rapido", params
        )
        catalogo = catalogo.merge(ventas, on="cod_rapido", how="left")
        catalogo["venta"] = pd.to_numeric(catalogo["venta"], errors="coerce").fillna(0.0)
        catalogo["stock"] = pd.to_numeric(catalogo["stock"], errors="coerce").fillna(0.0)
        catalogo["con_venta"] = catalogo["venta"] > 0
        df = (catalogo.groupby(["familia", "categoria", "jefe_linea"], as_index=False)
              .agg(venta=("venta", "sum"),
                   skus_asociados=("cod_rapido", "nunique"),
                   skus_con_venta=("con_venta", "sum"),
                   stock=("stock", "sum")))
        total = float(df["venta"].sum()) or 1.0
        df["participacion_pct"] = df["venta"] / total * 100
        return df.sort_values(["venta", "skus_asociados"], ascending=[False, False]).head(n)

    # Respaldo para bases antiguas: solo categorías con venta conocidas desde el hecho.
    where, params = _where(cod_tienda, anio, mes, semana, filtros)
    where, params = _agregar_seccion(where, params, pasillo, rack)
    q = (f"SELECT COALESCE(d.familia,'(sin familia)') AS familia, "
         f"COALESCE(d.categoria,'(sin categoría)') AS categoria, "
         f"COALESCE(d.responsable_linea,'(sin jefe de línea)') AS jefe_linea, "
         f"SUM(f.venta) AS venta, COUNT(DISTINCT f.cod_rapido) AS skus_asociados "
         f"{_JOIN} WHERE {where} AND f.venta > 0 "
         f"GROUP BY d.familia, d.categoria, d.responsable_linea ORDER BY venta DESC")
    df = _df(q, params)
    if df.empty:
        return df
    df["venta"] = pd.to_numeric(df["venta"], errors="coerce").fillna(0)
    df["skus_con_venta"] = df["skus_asociados"]
    df["stock"] = pd.NA
    total = float(df["venta"].sum()) or 1.0
    df["participacion_pct"] = df["venta"] / total * 100
    return df.head(n)


def get_detalle_productos(cod_tienda, anio, mes=None, semana=None, filtros=None,
                          pasillo=None, rack=None):
    """Detalle completo descargable del período/selección, sin límite top-N."""
    where, params = _where(cod_tienda, anio, mes, semana, filtros)
    where, params = _agregar_seccion(where, params, pasillo, rack)
    q = (f"SELECT f.pasillo, f.rack, f.cod_rapido, d.descripcion, d.marca, "
         f"d.familia, d.subfamilia, d.categoria, d.clasificacion, d.responsable_linea, "
         f"d.zona_pck, d.maneja_stock, d.stock, "
         f"SUM(f.venta) AS venta, SUM(f.cantidad) AS cantidad "
         f"{_JOIN} WHERE {where} "
         f"GROUP BY f.pasillo, f.rack, f.cod_rapido, d.descripcion, d.marca, "
         f"d.familia, d.subfamilia, d.categoria, d.clasificacion, d.responsable_linea, "
         f"d.zona_pck, d.maneja_stock, d.stock ORDER BY venta DESC")
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


def get_acciones_producto(cod_tienda, anio, filtros=None, n=300, pasillo=None, rack=None):
    """Cola de productos con stock pero sin venta en el año, priorizada y explicable."""
    filtro_extra = ""
    params = {"t": cod_tienda, "a": anio}
    for key, col in FILTER_COLS.items():
        vals = (filtros or {}).get(key)
        if vals:
            filtro_extra += f" AND d.{col.split('.')[1]} = ANY(%({key})s)"
            params[key] = list(vals)
    if pasillo:
        filtro_extra += " AND d.pasillo = %(pasillo)s"
        params["pasillo"] = pasillo
    if rack:
        filtro_extra += " AND d.rack = %(rack)s"
        params["rack"] = rack

    limit_sql = ""
    if n is not None:
        params["n"] = int(n)
        limit_sql = "LIMIT %(n)s"

    q = f"""
    WITH sin_venta AS (
        SELECT d.cod_rapido, d.descripcion, d.marca, d.stock, d.familia, d.categoria,
               d.responsable_linea, d.pasillo, d.rack
        FROM dim_producto_tienda d
        WHERE d.cod_tienda = %(t)s AND d.maneja_stock = 'S' {filtro_extra}
          AND COALESCE(d.stock,0) > 0
          AND NOT EXISTS (
              SELECT 1 FROM fact_venta_semana f
              WHERE f.cod_tienda = d.cod_tienda AND f.cod_rapido = d.cod_rapido
                AND f.anio = %(a)s AND f.venta > 0
          )
    ),
    historial AS (
        SELECT cod_rapido,
               MAX(CASE WHEN anio = %(a)s - 1 THEN venta ELSE 0 END) AS venta_anio_anterior
        FROM fact_producto_anio WHERE cod_tienda = %(t)s
        GROUP BY cod_rapido
    ),
    venta_categoria AS (
        SELECT d.categoria, SUM(f.venta) AS venta_categoria
        FROM fact_venta_semana f JOIN dim_producto_tienda d
          ON d.cod_tienda = f.cod_tienda AND d.cod_rapido = f.cod_rapido
        WHERE f.cod_tienda = %(t)s AND f.anio = %(a)s
        GROUP BY d.categoria
    )
    SELECT sv.cod_rapido, sv.descripcion, sv.marca, sv.stock, sv.familia, sv.categoria,
           sv.responsable_linea, sv.pasillo, sv.rack,
           COALESCE(h.venta_anio_anterior, 0) AS venta_anio_anterior,
           COALESCE(vc.venta_categoria, 0) AS venta_categoria
    FROM sin_venta sv
    LEFT JOIN historial h ON h.cod_rapido = sv.cod_rapido
    LEFT JOIN venta_categoria vc ON vc.categoria = sv.categoria
    ORDER BY h.venta_anio_anterior DESC NULLS LAST, sv.stock DESC NULLS LAST
    {limit_sql}
    """
    df = _df(q, params)
    if df.empty:
        for c in ("prioridad", "accion", "motivo"):
            df[c] = []
        return df

    def _accion(row):
        if row["venta_anio_anterior"] and row["venta_anio_anterior"] > 0:
            return ("Alta", "Recuperar venta",
                    "Tiene stock, no vende este año y sí vendía el año anterior.")
        if row["venta_categoria"] and row["venta_categoria"] > 0:
            return ("Media", "Mejorar visibilidad",
                    "Su categoría sí vende; revisar ubicación, precio, disponibilidad o exhibición de este SKU.")
        return ("Baja", "Revisar permanencia",
                "No vende y su categoría también tiene baja actividad; validar surtido o liquidación.")

    out = df.apply(_accion, axis=1, result_type="expand")
    out.columns = ["prioridad", "accion", "motivo"]
    df = pd.concat([df, out], axis=1)
    order = {"Alta": 0, "Media": 1, "Baja": 2}
    df["_orden"] = df["prioridad"].map(order).fillna(9)
    return df.sort_values(["_orden", "venta_anio_anterior", "stock"], ascending=[True, False, False]) \
             .drop(columns="_orden")

def get_top_combos(cod_tienda, n=30, orden="boletas", pasillo=None, rack=None):
    col = {"boletas": "boletas", "lift": "lift", "confianza": "confianza_a_b"}.get(orden, "boletas")
    where = "cod_tienda=%(t)s"
    params = {"t": cod_tienda, "n": n}
    if pasillo or rack:
        # V6: al seleccionar una sección, cross-sell se acota al SURTIDO VIGENTE
        # de esa sección. No usa la ubicación histórica para no mezclar conceptos.
        cond = "d.pasillo = %(pasillo)s" if pasillo else "d.rack = %(rack)s"
        if pasillo:
            params["pasillo"] = pasillo
        if rack:
            params["rack"] = rack
        where += (f" AND (sku_a IN (SELECT DISTINCT cod_rapido FROM dim_producto_tienda d "
                  f"WHERE d.cod_tienda=%(t)s AND {cond}) "
                  f"OR sku_b IN (SELECT DISTINCT cod_rapido FROM dim_producto_tienda d "
                  f"WHERE d.cod_tienda=%(t)s AND {cond}))")
    return _df(
        f"SELECT desc_a, desc_b, boletas, soporte, confianza_a_b, lift FROM fact_cross_sell "
        f"WHERE {where} ORDER BY {col} DESC LIMIT %(n)s", params)


def get_productos_lista(cod_tienda, pasillo=None, rack=None):
    """Productos con relaciones de cross-sell; al elegir ubicación usa el surtido vigente de esa sección."""
    params = {"t": cod_tienda}
    filtro = ""
    if rack:
        params["rack"] = rack
        filtro = " AND x.sku IN (SELECT cod_rapido FROM dim_producto_tienda WHERE cod_tienda=%(t)s AND rack=%(rack)s)"
    elif pasillo:
        params["pasillo"] = pasillo
        filtro = " AND x.sku IN (SELECT cod_rapido FROM dim_producto_tienda WHERE cod_tienda=%(t)s AND pasillo=%(pasillo)s)"
    return _df(
        "SELECT DISTINCT x.sku, x.descripcion FROM ("
        "  SELECT sku_a AS sku, desc_a AS descripcion FROM fact_cross_sell WHERE cod_tienda=%(t)s "
        "  UNION SELECT sku_b, desc_b FROM fact_cross_sell WHERE cod_tienda=%(t)s"
        ") x WHERE COALESCE(x.descripcion,'')<>''" + filtro + " ORDER BY x.descripcion", params)


def get_combos_de_producto(cod_tienda, cod_rapido, n=15):
    """Relaciones dirigidas desde el producto seleccionado, con métricas accionables.

    compras_base se deriva como boletas/confianza. oportunidades_sin_complemento
    representa compras del producto seleccionado donde el complemento NO apareció;
    es una oportunidad de prueba de cross-sell, no una venta incremental esperada.
    """
    return _df(
        "WITH rel AS ("
        " SELECT CASE WHEN sku_a=%(sku)s THEN desc_b ELSE desc_a END AS producto, boletas, "
        " CASE WHEN sku_a=%(sku)s THEN confianza_a_b ELSE confianza_b_a END AS confianza, lift "
        " FROM fact_cross_sell WHERE cod_tienda=%(t)s AND (sku_a=%(sku)s OR sku_b=%(sku)s)"
        ") "
        "SELECT producto, boletas, confianza, lift, "
        " CASE WHEN lift>0 THEN confianza/lift ELSE NULL END AS frecuencia_base, "
        " CASE WHEN confianza>0 THEN ROUND(boletas/confianza) ELSE NULL END AS compras_producto, "
        " CASE WHEN confianza>0 THEN GREATEST(ROUND(boletas/confianza)-boletas,0) ELSE NULL END AS oportunidades_sin_complemento "
        "FROM rel ORDER BY boletas DESC LIMIT %(n)s",
        {"t": cod_tienda, "sku": cod_rapido, "n": n})



def get_pasillos_disponibles(cod_tienda):
    """Pasillos conocidos: surtido vigente + historia física ya preservada en Postgres."""
    return _df(
        "SELECT pasillo FROM ("
        " SELECT DISTINCT pasillo FROM dim_producto_tienda WHERE cod_tienda=%(t)s AND COALESCE(pasillo,'')<>'' "
        " UNION "
        " SELECT DISTINCT pasillo FROM fact_venta_rack_dia WHERE cod_tienda=%(t)s AND COALESCE(pasillo,'')<>'' "
        ") x ORDER BY pasillo", {"t": cod_tienda})


def get_racks_disponibles(cod_tienda, pasillo=None):
    """Racks conocidos, opcionalmente acotados a un pasillo."""
    params = {"t": cod_tienda}
    cond_dim = ""
    cond_fact = ""
    if pasillo:
        params["p"] = str(pasillo)
        cond_dim = " AND pasillo=%(p)s"
        cond_fact = " AND pasillo=%(p)s"
    return _df(
        "SELECT rack FROM ("
        " SELECT DISTINCT rack FROM dim_producto_tienda WHERE cod_tienda=%(t)s AND COALESCE(rack,'')<>''" + cond_dim +
        " UNION "
        " SELECT DISTINCT rack FROM fact_venta_rack_dia WHERE cod_tienda=%(t)s AND COALESCE(rack,'')<>''" + cond_fact +
        ") x ORDER BY rack", params)

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


# ============================================================================
# V6 — análisis físico reciente vs surtido vigente
# ============================================================================
_JOIN_FISICO = (
    "FROM fact_venta_rack_dia f JOIN dim_producto_tienda d "
    "ON d.cod_tienda=f.cod_tienda AND d.cod_rapido=f.cod_rapido"
)


def _rango_periodo(anio, mes=None, semana=None):
    """Rango calendario teórico del período seleccionado."""
    anio = int(anio)
    if semana:
        ini = datetime.date.fromisocalendar(anio, int(semana), 1)
        return ini, ini + datetime.timedelta(days=6)
    if mes:
        mes = int(mes)
        ini = datetime.date(anio, mes, 1)
        fin = datetime.date(anio, mes, calendar.monthrange(anio, mes)[1])
        return ini, fin
    return datetime.date(anio, 1, 1), datetime.date(anio, 12, 31)


def get_contexto_ubicacion_fisica(cod_tienda, anio, mes=None, semana=None):
    """Indica si el período está cubierto por la ubicación física histórica.

    INFSTOCK conserva una ventana corta (aprox. 3 meses). Para el mes/semana
    actual, el fin del período se recorta al último día realmente cargado.
    El modo Año nunca se marca como físico porque no existe cobertura anual.
    """
    try:
        d = _df(
            "SELECT fecha_desde AS desde, fecha_hasta AS hasta, cobertura_venta_pct "
            "FROM sync_ubicacion_fisica WHERE cod_tienda=%(t)s", {"t": cod_tienda}
        )
        if d.empty:
            d = _df(
                "SELECT MIN(fecha) AS desde, MAX(fecha) AS hasta, NULL::numeric AS cobertura_venta_pct "
                "FROM fact_venta_rack_dia WHERE cod_tienda=%(t)s", {"t": cod_tienda}
            )
        d = d.iloc[0]
    except Exception:
        return {"cubierto": False, "desde": None, "hasta": None, "cobertura_venta_pct": None,
                "periodo_desde": None, "periodo_hasta": None,
                "motivo": "Aún no se ha cargado el historial físico reciente."}
    if pd.isna(d.get("desde")) or pd.isna(d.get("hasta")):
        return {"cubierto": False, "desde": None, "hasta": None, "cobertura_venta_pct": None,
                "periodo_desde": None, "periodo_hasta": None,
                "motivo": "Aún no se ha cargado el historial físico reciente."}

    desde = pd.to_datetime(d["desde"]).date()
    hasta = pd.to_datetime(d["hasta"]).date()
    ini, fin_teorico = _rango_periodo(anio, mes, semana)
    fin = min(fin_teorico, hasta)
    es_anio = not mes and not semana
    cubierto = (not es_anio) and ini >= desde and ini <= fin and fin <= hasta
    if es_anio:
        motivo = "La ubicación física solo existe para la ventana reciente; el año completo no es comparable por rack físico."
    elif ini < desde:
        motivo = f"El período comienza antes del historial físico disponible ({desde:%d/%m/%Y})."
    elif ini > hasta:
        motivo = f"El período es posterior al último historial físico disponible ({hasta:%d/%m/%Y})."
    else:
        motivo = "Período cubierto por el historial físico disponible."
    cov = d.get("cobertura_venta_pct") if hasattr(d, "get") else None
    cov = None if cov is None or pd.isna(cov) else float(cov)
    return {"cubierto": bool(cubierto), "desde": desde, "hasta": hasta, "cobertura_venta_pct": cov,
            "periodo_desde": ini, "periodo_hasta": fin, "motivo": motivo}


def _filtros_dim(filtros, alias="d"):
    cond, params = [], {}
    for key, col in FILTER_COLS.items():
        vals = (filtros or {}).get(key)
        if vals:
            col_only = col.split(".")[1]
            cond.append(f"{alias}.{col_only} = ANY(%({key})s)")
            params[key] = list(vals)
    return cond, params


def _where_fisico(cod_tienda, fecha_ini, fecha_fin, filtros=None, pasillo=None, rack=None):
    where = ["f.cod_tienda=%(t)s", "f.fecha >= %(fi)s", "f.fecha <= %(ff)s"]
    params = {"t": cod_tienda, "fi": fecha_ini, "ff": fecha_fin}
    extra, fp = _filtros_dim(filtros, "d")
    where += extra
    params.update(fp)
    if pasillo:
        where.append("f.pasillo=%(pasillo)s")
        params["pasillo"] = pasillo
    if rack:
        where.append("f.rack=%(rack)s")
        params["rack"] = rack
    return " AND ".join(where), params


def _rango_anterior_fisico(ctx, mes=None, semana=None):
    """Período inmediatamente anterior, con el mismo número de días si el actual es parcial."""
    if not ctx or not ctx.get("cubierto"):
        return None
    ini, fin = ctx["periodo_desde"], ctx["periodo_hasta"]
    dias = (fin - ini).days + 1
    if semana:
        prev_ini = ini - datetime.timedelta(days=7)
        prev_fin = prev_ini + datetime.timedelta(days=dias - 1)
    elif mes:
        if ini.month == 1:
            py, pm = ini.year - 1, 12
        else:
            py, pm = ini.year, ini.month - 1
        prev_ini = datetime.date(py, pm, 1)
        prev_fin = min(prev_ini + datetime.timedelta(days=dias - 1),
                       datetime.date(py, pm, calendar.monthrange(py, pm)[1]))
    else:
        return None
    if prev_ini < ctx["desde"] or prev_fin > ctx["hasta"]:
        return None
    return prev_ini, prev_fin


def get_resumen_fisico(cod_tienda, anio, mes=None, semana=None, filtros=None, pasillo=None, rack=None):
    ctx = get_contexto_ubicacion_fisica(cod_tienda, anio, mes, semana)
    if not ctx.get("cubierto"):
        return None
    where, params = _where_fisico(cod_tienda, ctx["periodo_desde"], ctx["periodo_hasta"], filtros, pasillo, rack)
    cur = _df(
        f"SELECT COALESCE(SUM(f.venta),0) AS venta, COALESCE(SUM(f.cantidad),0) AS unidades, "
        f"COUNT(DISTINCT f.pasillo) AS pasillos, COUNT(DISTINCT f.rack) AS racks, "
        f"COUNT(DISTINCT f.cod_rapido) AS skus {_JOIN_FISICO} WHERE {where}", params
    ).iloc[0].to_dict()
    prev = _rango_anterior_fisico(ctx, mes, semana)
    prev_venta = None
    prev_label = None
    if prev:
        wp, pp = _where_fisico(cod_tienda, prev[0], prev[1], filtros, pasillo, rack)
        pv = _df(f"SELECT COALESCE(SUM(f.venta),0) AS venta {_JOIN_FISICO} WHERE {wp}", pp).iloc[0]["venta"]
        prev_venta = float(pv or 0)
        if semana:
            prev_label = f"Semana anterior ({prev[0]:%d/%m}–{prev[1]:%d/%m})"
        else:
            prev_label = f"Período anterior ({prev[0]:%d/%m}–{prev[1]:%d/%m})"
    cur["venta_anterior"] = prev_venta
    cur["periodo_anterior"] = prev_label
    cur["variacion_pct"] = ((float(cur["venta"]) - prev_venta) / prev_venta * 100) if prev_venta not in (None, 0) else None
    cur["contexto_fisico"] = ctx
    return cur


def get_pasillo_resumen_fisico(cod_tienda, anio, mes=None, semana=None, filtros=None, pasillo=None, rack=None):
    ctx = get_contexto_ubicacion_fisica(cod_tienda, anio, mes, semana)
    if not ctx.get("cubierto"):
        return pd.DataFrame()
    where, params = _where_fisico(cod_tienda, ctx["periodo_desde"], ctx["periodo_hasta"], filtros, pasillo, rack)
    return _df(
        f"SELECT f.pasillo, SUM(f.venta) AS venta, SUM(f.cantidad) AS unidades, "
        f"COUNT(DISTINCT f.rack) AS racks, COUNT(DISTINCT f.cod_rapido) AS skus "
        f"{_JOIN_FISICO} WHERE {where} GROUP BY f.pasillo ORDER BY venta DESC", params)


def get_rack_detalle_fisico(cod_tienda, anio, mes=None, semana=None, filtros=None, pasillo=None, rack=None):
    ctx = get_contexto_ubicacion_fisica(cod_tienda, anio, mes, semana)
    if not ctx.get("cubierto"):
        return pd.DataFrame()
    where, params = _where_fisico(cod_tienda, ctx["periodo_desde"], ctx["periodo_hasta"], filtros, pasillo, rack)
    return _df(
        f"SELECT f.pasillo, f.rack, SUM(f.venta) AS venta, SUM(f.cantidad) AS unidades, "
        f"COUNT(DISTINCT f.cod_rapido) AS skus {_JOIN_FISICO} WHERE {where} "
        f"GROUP BY f.pasillo, f.rack ORDER BY venta DESC", params)


def get_venta_por_nivel_fisico(cod_tienda, anio, mes=None, semana=None, filtros=None, nivel="pasillo"):
    ctx = get_contexto_ubicacion_fisica(cod_tienda, anio, mes, semana)
    if not ctx.get("cubierto"):
        return pd.DataFrame(columns=["clave", "venta"])
    if nivel not in ("pasillo", "rack"):
        nivel = "pasillo"
    where, params = _where_fisico(cod_tienda, ctx["periodo_desde"], ctx["periodo_hasta"], filtros)
    return _df(f"SELECT f.{nivel} AS clave, SUM(f.venta) AS venta {_JOIN_FISICO} WHERE {where} GROUP BY f.{nivel}", params)


def get_top_productos_fisico(cod_tienda, anio, mes=None, semana=None, filtros=None,
                              pasillo=None, rack=None, n=50, ascendente=False):
    ctx = get_contexto_ubicacion_fisica(cod_tienda, anio, mes, semana)
    if not ctx.get("cubierto"):
        return pd.DataFrame()
    where, params = _where_fisico(cod_tienda, ctx["periodo_desde"], ctx["periodo_hasta"], filtros, pasillo, rack)
    params["n"] = n
    orden = "ASC" if ascendente else "DESC"
    having = "HAVING SUM(f.venta) > 0" if ascendente else ""
    return _df(
        f"SELECT f.cod_rapido, d.descripcion, d.marca, d.familia, d.categoria, d.responsable_linea, "
        f"CASE d.maneja_stock WHEN 'S' THEN 'Sí' WHEN 'N' THEN 'No' ELSE d.maneja_stock END AS maneja_stock, "
        f"d.stock, SUM(f.venta) AS venta, SUM(f.cantidad) AS cantidad {_JOIN_FISICO} WHERE {where} "
        f"GROUP BY f.cod_rapido,d.descripcion,d.marca,d.familia,d.categoria,d.responsable_linea,d.maneja_stock,d.stock "
        f"{having} ORDER BY venta {orden} LIMIT %(n)s", params)


def get_treemap_fisico(cod_tienda, anio, mes=None, semana=None, filtros=None, pasillo=None, rack=None):
    ctx = get_contexto_ubicacion_fisica(cod_tienda, anio, mes, semana)
    if not ctx.get("cubierto"):
        return pd.DataFrame()
    where, params = _where_fisico(cod_tienda, ctx["periodo_desde"], ctx["periodo_hasta"], filtros, pasillo, rack)
    return _df(
        f"SELECT COALESCE(d.familia,'(sin familia)') AS familia, "
        f"COALESCE(d.responsable_linea,'(sin jefe de línea)') AS jefe_linea, "
        f"COALESCE(d.categoria,'(sin categoría)') AS categoria, SUM(f.venta) AS venta "
        f"{_JOIN_FISICO} WHERE {where} AND f.venta > 0 "
        f"GROUP BY d.familia,d.responsable_linea,d.categoria", params)


def get_tendencia_semana_fisico(cod_tienda, filtros=None, pasillo=None, rack=None):
    try:
        rango = _df("SELECT MIN(fecha) desde, MAX(fecha) hasta FROM fact_venta_rack_dia WHERE cod_tienda=%(t)s",
                    {"t": cod_tienda}).iloc[0]
    except Exception:
        return pd.DataFrame()
    if pd.isna(rango["desde"]) or pd.isna(rango["hasta"]):
        return pd.DataFrame()
    where, params = _where_fisico(cod_tienda, pd.to_datetime(rango["desde"]).date(),
                                   pd.to_datetime(rango["hasta"]).date(), filtros, pasillo, rack)
    return _df(f"SELECT f.anio, f.semana, MIN(f.fecha) AS desde, MAX(f.fecha) AS hasta, SUM(f.venta) AS venta "
               f"{_JOIN_FISICO} WHERE {where} GROUP BY f.anio,f.semana ORDER BY f.anio,f.semana", params)


def get_acciones_rack_fisico(cod_tienda, anio, mes=None, semana=None, filtros=None):
    """Recomendaciones de ESPACIO solo cuando la ubicación física está cubierta.

    Compara contra el período inmediatamente anterior dentro de la misma ventana
    física; nunca usa el año anterior porque INFSTOCK ya no conserva esa ubicación.
    """
    ctx = get_contexto_ubicacion_fisica(cod_tienda, anio, mes, semana)
    if not ctx.get("cubierto"):
        return pd.DataFrame()
    where, params = _where_fisico(cod_tienda, ctx["periodo_desde"], ctx["periodo_hasta"], filtros)
    df = _df(
        f"SELECT f.pasillo,f.rack,SUM(f.venta) AS venta,SUM(f.cantidad) AS unidades,"
        f"COUNT(DISTINCT f.cod_rapido) AS skus {_JOIN_FISICO} WHERE {where} GROUP BY f.pasillo,f.rack", params)
    if df.empty:
        return df
    for c in ("venta", "unidades", "skus"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df["skus"] = df["skus"].astype(int)
    df["venta_por_sku"] = df["venta"] / df["skus"].replace(0, pd.NA)
    df["percentil_venta"] = df["venta"].rank(pct=True, method="average") * 100

    prev = _rango_anterior_fisico(ctx, mes, semana)
    if prev:
        wp, pp = _where_fisico(cod_tienda, prev[0], prev[1], filtros)
        ant = _df(f"SELECT f.pasillo,f.rack,SUM(f.venta) AS venta_anterior {_JOIN_FISICO} "
                  f"WHERE {wp} GROUP BY f.pasillo,f.rack", pp)
        df = df.merge(ant, on=["pasillo","rack"], how="left")
        comp_label = f"{prev[0]:%d/%m}–{prev[1]:%d/%m}"
    else:
        df["venta_anterior"] = pd.NA
        comp_label = None
    df["venta_anterior"] = pd.to_numeric(df["venta_anterior"], errors="coerce")
    df["variacion_pct"] = ((df["venta"] - df["venta_anterior"]) / df["venta_anterior"].replace(0, pd.NA)) * 100
    df["comparacion_label"] = comp_label

    # Surtido vigente por rack: sirve solo para detectar racks que cambiaron/desaparecieron.
    dim_cond, dim_params = _filtros_dim(filtros, "d")
    dim_where = ["d.cod_tienda=%(t)s"] + dim_cond
    dim_params["t"] = cod_tienda
    surt = _df("SELECT d.rack, COUNT(DISTINCT d.cod_rapido) AS skus_asociados_hoy "
               f"FROM dim_producto_tienda d WHERE {' AND '.join(dim_where)} AND COALESCE(d.rack,'')<>'' GROUP BY d.rack",
               dim_params)
    df = df.merge(surt, on="rack", how="left")
    df["skus_asociados_hoy"] = pd.to_numeric(df["skus_asociados_hoy"], errors="coerce").fillna(0).astype(int)

    p30, p50, p70 = [float(df["venta"].quantile(q)) for q in (0.30,0.50,0.70)]
    sku70 = float(df["skus"].quantile(0.70))

    def clas(r):
        if int(r["skus_asociados_hoy"]) == 0:
            return ("Info", "Rack cambió",
                    "Tuvo venta física en el período, pero hoy no tiene SKU asociados con ese código de rack.",
                    "Validar cambio de planograma/codificación. No usar este caso para decidir espacio actual.")
        var = r["variacion_pct"]
        tiene = pd.notna(var)
        if r["venta"] >= p70 and tiene and var <= -10:
            return ("Alta", "Proteger desempeño", f"Rack top 30% en venta física, pero cae {abs(var):.1f}% vs período anterior.",
                    "Revisar disponibilidad, precio y cambios recientes de surtido/exhibición.")
        if r["venta"] <= p30 and tiene and var <= -10:
            return ("Alta", "Revisar espacio", f"Rack de baja venta física y cae {abs(var):.1f}% vs período anterior.",
                    "Revisar si el espacio y el mix actual siguen justificándose antes de reducir o mover.")
        if r["venta"] >= p70 and tiene and var >= 10:
            return ("Media", "Potenciar espacio", f"Rack top 30% en venta física y crece {var:.1f}% vs período anterior.",
                    "Asegurar stock de líderes y evaluar mayor visibilidad si el layout lo permite.")
        if r["venta"] <= p50 and r["skus"] >= sku70:
            return ("Media", "Revisar mix", f"Vendieron {int(r['skus'])} SKU, pero el rack queda bajo la mediana de venta física.",
                    "Revisar concentración del surtido; no implica automáticamente reducir espacio.")
        return ("Baja", "Mantener", "Sin señal física fuerte de riesgo u oportunidad en la ventana comparable.",
                "Monitorear; no hay acción urgente.")

    cl = df.apply(clas, axis=1, result_type="expand")
    cl.columns = ["prioridad","accion","motivo","recomendacion"]
    df = pd.concat([df,cl],axis=1)
    order = {"Alta":4,"Media":3,"Info":2,"Baja":1}
    df["score_orden"] = df["prioridad"].map(order).fillna(0)*100 + df["percentil_venta"]
    return df.sort_values(["score_orden","venta"], ascending=[False,False]).reset_index(drop=True)


def get_categorias_surtido_actual(cod_tienda, filtros=None, pasillo=None, rack=None, n=8):
    where = ["d.cod_tienda=%(t)s"]
    params = {"t": cod_tienda, "n": n}
    extra, fp = _filtros_dim(filtros, "d")
    where += extra; params.update(fp)
    if pasillo:
        where.append("d.pasillo=%(pasillo)s"); params["pasillo"] = pasillo
    if rack:
        where.append("d.rack=%(rack)s"); params["rack"] = rack
    return _df(
        "SELECT COALESCE(d.familia,'(sin familia)') AS familia, COALESCE(d.categoria,'(sin categoría)') AS categoria, "
        "COUNT(DISTINCT d.cod_rapido) AS skus_asociados, "
        "COUNT(DISTINCT CASE WHEN COALESCE(d.stock,0)>0 THEN d.cod_rapido END) AS skus_con_stock, "
        "COALESCE(SUM(d.stock),0) AS stock "
        f"FROM dim_producto_tienda d WHERE {' AND '.join(where)} GROUP BY d.familia,d.categoria "
        "ORDER BY skus_asociados DESC LIMIT %(n)s", params)


def get_categorias_venta_fisica(cod_tienda, anio, mes=None, semana=None, filtros=None, pasillo=None, rack=None, n=8):
    ctx = get_contexto_ubicacion_fisica(cod_tienda, anio, mes, semana)
    if not ctx.get("cubierto") or (not pasillo and not rack):
        return pd.DataFrame()
    where, params = _where_fisico(cod_tienda, ctx["periodo_desde"], ctx["periodo_hasta"], filtros, pasillo, rack)
    params["n"] = n
    df = _df(
        f"SELECT COALESCE(d.familia,'(sin familia)') AS familia, COALESCE(d.categoria,'(sin categoría)') AS categoria, "
        f"SUM(f.venta) AS venta, COUNT(DISTINCT f.cod_rapido) AS skus_con_venta {_JOIN_FISICO} WHERE {where} "
        f"GROUP BY d.familia,d.categoria ORDER BY venta DESC LIMIT %(n)s", params)
    if not df.empty:
        total = float(pd.to_numeric(df["venta"], errors="coerce").fillna(0).sum()) or 1
        df["participacion_pct"] = pd.to_numeric(df["venta"], errors="coerce").fillna(0)/total*100
    return df


def get_sin_coordenadas_fisico(cod_tienda, anio, mes=None, semana=None, nivel="pasillo"):
    ctx = get_contexto_ubicacion_fisica(cod_tienda, anio, mes, semana)
    if not ctx.get("cubierto"):
        return pd.DataFrame(columns=["clave","venta"])
    tabla = "dim_pasillo_coord" if nivel == "pasillo" else "dim_rack_coord"
    col = "pasillo" if nivel == "pasillo" else "rack"
    q = (f"SELECT f.{col} AS clave, SUM(f.venta) AS venta FROM fact_venta_rack_dia f "
         f"WHERE f.cod_tienda=%(t)s AND f.fecha>=%(fi)s AND f.fecha<=%(ff)s AND COALESCE(f.{col},'')<>'' "
         f"AND NOT EXISTS (SELECT 1 FROM {tabla} c WHERE c.cod_tienda=f.cod_tienda AND c.{col}=f.{col}) "
         f"GROUP BY f.{col} ORDER BY venta DESC")
    return _df(q, {"t":cod_tienda,"fi":ctx["periodo_desde"],"ff":ctx["periodo_hasta"]})


def get_detalle_productos_fisico(cod_tienda, anio, mes=None, semana=None, filtros=None, pasillo=None, rack=None):
    ctx = get_contexto_ubicacion_fisica(cod_tienda, anio, mes, semana)
    if not ctx.get("cubierto"):
        return pd.DataFrame()
    where, params = _where_fisico(cod_tienda, ctx["periodo_desde"], ctx["periodo_hasta"], filtros, pasillo, rack)
    return _df(
        f"SELECT f.pasillo,f.rack,f.cod_rapido,d.descripcion,d.marca,d.familia,d.subfamilia,d.categoria,"
        f"d.clasificacion,d.responsable_linea,d.zona_pck,d.maneja_stock,d.stock,"
        f"SUM(f.venta) AS venta,SUM(f.cantidad) AS cantidad {_JOIN_FISICO} WHERE {where} "
        f"GROUP BY f.pasillo,f.rack,f.cod_rapido,d.descripcion,d.marca,d.familia,d.subfamilia,d.categoria,"
        f"d.clasificacion,d.responsable_linea,d.zona_pck,d.maneja_stock,d.stock ORDER BY venta DESC", params)
