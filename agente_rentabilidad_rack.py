"""
Agente de sincronización — SQL Server → Postgres (Railway)

Se agenda con el Task Scheduler de Windows en un PC/servidor con acceso a tu
SQL Server. Trae el año actual completo y lo sube con INSERT ... ON CONFLICT
(upsert) -- se puede correr todos los días sin duplicar nada.

CONFIGURAR:
  pip install pyodbc psycopg2-binary pandas
  Variables de entorno:
    SQLSERVER_DSN   -- cadena de conexión ODBC a tu SQL Server
    DATABASE_URL    -- la de Railway (Postgres → Connect)

CAMPOS CONFIRMADOS POR JAVIER (2026):
  - Marca y Stock_Disponible: nombres correctos en SAV_CI_INFSTOCKBODEGA_DIARIO.
  - Cliente/RUT en FCVCab/BLVcab: se llama cod_entidad (usado para clientes únicos).

PASILLO/RACK -- columna calculada Etiqueta_base del modelo original, validada
contra 6.2M filas reales (99.2% de coincidencia exacta):
    usa_exhibicion = Zona_pck IN ('Z03','Z04','Z06') OR ManejaStock = 'N'
    Etiqueta_base  = si usa_exhibicion: Etiqueta_Exhibicion (o Etiqueta si vacía)
                     si no:             Etiqueta (o Etiqueta_Exhibicion si vacía)
    Pasillo = Etiqueta_base[:3]   rack = Etiqueta_base[:8]

CROSS-SELL: este agente recalcula fact_cross_sell completa cada vez que corre
(soporte/confianza/lift por par de SKU, a partir de las boletas del año). Es
la parte más pesada -- no pude medir cuánto demora contra tu volumen real de
SQL Server; en el ambiente de prueba, calcularlo desde ~900 mil boletas tomó
unos segundos en Python puro. Si notas que se pone lento, lo primero que
probaría es acotarlo a los últimos 90-180 días en vez del año completo.

TABLA DE COORDENADAS (Pasillos / Puntos Planograma): no sale de SQL Server --
el Power Query original la trae de "coordenadas planograma.xlsx" (OneDrive de
un usuario "mriosv"). Este agente no la toca; usa la página Administrar
Planos para subir plano+coordenadas de cada tienda.

NO PROBADO CONTRA TU SQL SERVER REAL -- no tengo acceso a tu red. Corre una
prueba manual primero y avísame si algún nombre de columna no calza.
"""
import os
import sys
import datetime
import itertools
import collections
import pandas as pd
import pyodbc
import psycopg2
import psycopg2.extras

SQLSERVER_DSN = os.environ.get(
    "SQLSERVER_DSN",
    "DRIVER={ODBC Driver 17 for SQL Server};SERVER=<tu_servidor>;DATABASE=SAV_VT;"
    "UID=<tu_usuario>;PWD=<tu_password>;",
)
DATABASE_URL = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)

STORE_CODES = ['SANRO', 'SEREN', 'MAIPU', 'TEMUC', 'RENAC', 'CONCE',
               'VALPA', 'TALCA', 'RANCA', 'MAPOC', 'HUECH', 'PMONT', 'VESPU']

QUERY = f"""
DECLARE @IniAnio date = DATEFROMPARTS(YEAR(GETDATE()), 1, 1);

WITH StockRank AS (
    SELECT
        s.Cod_Rapido,
        CASE s.cod_emp
            WHEN 'VESPUCIO'  THEN 'VESPU' WHEN 'SERENA'    THEN 'SEREN'
            WHEN 'MAIPU'     THEN 'MAIPU' WHEN 'TEMUCO'    THEN 'TEMUC'
            WHEN 'RENACA'    THEN 'RENAC' WHEN 'CONCE'     THEN 'CONCE'
            WHEN 'VALPO'     THEN 'VALPA' WHEN 'MADERA'    THEN 'SANRO'
            WHEN 'TALCA'     THEN 'TALCA' WHEN 'RANCAGUA'  THEN 'RANCA'
            WHEN 'MADIMP'    THEN 'MAPOC' WHEN 'FONTOVA'   THEN 'HUECH'
            WHEN 'PTOMONTT'  THEN 'PMONT' ELSE s.cod_emp
        END AS cod_emp,
        s.Etiqueta_Exhibicion, s.Etiqueta, s.Zona_pck, s.ManejaStock, s.Marca, s.Stock_Disponible,
        s.SuperFamilia AS familia, s.Familia AS subfamilia, s.Subfamilia AS categoria,
        s.Clasificacion, s.Responsable_Linea AS responsable_linea,
        ROW_NUMBER() OVER (PARTITION BY s.Cod_Rapido, s.cod_emp ORDER BY s.Fecha_Proceso DESC) AS rn
    FROM SAV.dbo.SAV_CI_INFSTOCKBODEGA_DIARIO s
    WHERE CAST(s.Fecha_Proceso AS date) >= CAST(DATEADD(DAY, -1, GETDATE()) AS date)
),
UltimosRegistros AS (SELECT * FROM StockRank WHERE rn = 1),
VentasDetalladas AS (
    SELECT f.cod_tienda, d.Cod_rapido, d.Descripcion, d.Cantidad, d.total, f.Fecha_Emision,
           f.nro_impreso, f.cod_entidad
    FROM SAV_VT.dbo.SAV_VT_FCVCab f
    INNER JOIN SAV_VT.dbo.SAV_VT_FCVdet d WITH (NOLOCK)
        ON f.Tipo_Documento = d.Tipo_Documento AND f.Nro_Interno = d.Nro_Interno AND f.Cod_Emp = d.Cod_Emp
    WHERE RTRIM(LTRIM(f.cod_tienda)) IN ({','.join(f"'{c}'" for c in STORE_CODES)})
      AND f.Fecha_Emision >= @IniAnio
    UNION ALL
    SELECT b.cod_tienda, e.Cod_rapido, e.Descripcion, e.Cantidad, e.total, b.Fecha_Emision,
           b.nro_impreso, b.cod_entidad
    FROM SAV_VT.dbo.SAV_VT_BLVcab b
    INNER JOIN SAV_VT.dbo.SAV_VT_BLVdet e WITH (NOLOCK)
        ON b.Tipo_Documento = e.Tipo_Documento AND b.Nro_Interno = e.Nro_Interno AND b.Cod_Emp = e.Cod_Emp
    WHERE RTRIM(LTRIM(b.cod_tienda)) IN ({','.join(f"'{c}'" for c in STORE_CODES)})
      AND b.Fecha_Emision >= @IniAnio
)
SELECT
    RTRIM(LTRIM(v.cod_tienda)) AS cod_tienda, v.Cod_rapido AS cod_rapido, v.Descripcion AS descripcion,
    v.Fecha_Emision AS fecha_emision, v.Cantidad AS cantidad, v.total AS total,
    v.nro_impreso, v.cod_entidad,
    s.Etiqueta_Exhibicion AS etiqueta_exhibicion, s.Etiqueta AS etiqueta,
    s.Zona_pck AS zona_pck, s.ManejaStock AS maneja_stock, s.Marca AS marca, s.Stock_Disponible AS stock,
    s.familia, s.subfamilia, s.categoria, s.Clasificacion AS clasificacion, s.responsable_linea
FROM VentasDetalladas v
LEFT JOIN UltimosRegistros s
    ON v.Cod_rapido = s.Cod_Rapido AND RTRIM(LTRIM(v.cod_tienda)) = s.cod_emp;
"""


def calcular_pasillo_rack(df):
    etq = df["etiqueta"].fillna("").astype(str).str.strip()
    exh = df["etiqueta_exhibicion"].fillna("").astype(str).str.strip()
    usa_exh = df["zona_pck"].isin(["Z03", "Z04", "Z06"]) | (df["maneja_stock"] == "N")
    base = pd.Series("", index=df.index, dtype=object)
    base[usa_exh] = exh[usa_exh].where(exh[usa_exh] != "", etq[usa_exh])
    base[~usa_exh] = etq[~usa_exh].where(etq[~usa_exh] != "", exh[~usa_exh])
    df = df.copy()
    df["pasillo"] = base.str.slice(0, 3)
    df["rack"] = base.str.slice(0, 8)
    return df


def calcular_cross_sell(df):
    """Soporte/Confianza/Lift por par de SKU, a partir de las boletas (nro_impreso)."""
    filas = []
    for tienda, g in df.groupby("cod_tienda"):
        baskets = collections.defaultdict(set)
        for nro, sku in zip(g["nro_impreso"], g["cod_rapido"]):
            if pd.notna(nro):
                baskets[nro].add(sku)
        total = len(baskets)
        if total == 0:
            continue
        sku_count = collections.Counter()
        pair_count = collections.Counter()
        for skus in baskets.values():
            for s in skus:
                sku_count[s] += 1
            if 2 <= len(skus) <= 20:
                for a, b in itertools.combinations(sorted(skus), 2):
                    pair_count[(a, b)] += 1
        desc = g.drop_duplicates("cod_rapido").set_index("cod_rapido")["descripcion"]
        pares = sorted(pair_count.items(), key=lambda kv: kv[1], reverse=True)[:500]
        for (a, b), n_ab in pares:
            if n_ab < 3:
                continue
            n_a, n_b = sku_count[a], sku_count[b]
            soporte = n_ab / total
            conf_ab = n_ab / n_a if n_a else 0
            conf_ba = n_ab / n_b if n_b else 0
            lift = conf_ab / (n_b / total) if n_b else 0
            filas.append((tienda, a, b, desc.get(a), desc.get(b), n_ab,
                           round(soporte, 5), round(conf_ab, 4), round(conf_ba, 4), round(lift, 3)))
    return pd.DataFrame(filas, columns=["cod_tienda", "sku_a", "sku_b", "desc_a", "desc_b",
                                          "boletas", "soporte", "confianza_a_b", "confianza_b_a", "lift"])


def main():
    if not DATABASE_URL:
        print("Falta DATABASE_URL"); sys.exit(1)

    print(f"[{datetime.datetime.now()}] Conectando a SQL Server...")
    with pyodbc.connect(SQLSERVER_DSN) as cn:
        df = pd.read_sql(QUERY, cn)
    print(f"{len(df):,} filas traídas de SQL Server.")

    df["fecha_emision"] = pd.to_datetime(df["fecha_emision"])
    df["anio"] = df["fecha_emision"].dt.year
    df["mes"] = df["fecha_emision"].dt.month
    df["semana"] = df["fecha_emision"].dt.isocalendar().week
    df = calcular_pasillo_rack(df)
    df = df[df["pasillo"] != ""]

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # --- fact_venta_semana ---
    agg = df.groupby(["cod_tienda", "anio", "mes", "semana", "pasillo", "rack", "cod_rapido"],
                      as_index=False).agg(venta=("total", "sum"), cantidad=("cantidad", "sum"))
    rows = [(str(r.cod_tienda), int(r.anio), int(r.mes), int(r.semana), str(r.pasillo), str(r.rack),
              str(r.cod_rapido), float(r.venta), 0.0, float(r.cantidad))
             for r in agg.itertuples(index=False)]
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO fact_venta_semana (cod_tienda,anio,mes,semana,pasillo,rack,cod_rapido,venta,margen,cantidad) "
        "VALUES %s ON CONFLICT (cod_tienda,anio,mes,semana,pasillo,rack,cod_rapido) DO UPDATE "
        "SET venta = EXCLUDED.venta, cantidad = EXCLUDED.cantidad",
        rows, page_size=5000)
    print(f"fact_venta_semana: {len(rows):,} filas")

    # --- agregados anuales para recomendaciones (mantiene 2025 seed y refresca año actual) ---
    tiendas_df = sorted(df["cod_tienda"].dropna().astype(str).unique().tolist())
    for anio_actual in sorted(df["anio"].dropna().astype(int).unique().tolist()):
        # El dataframe trae el año completo a la fecha: reemplazar el agregado evita filas obsoletas
        # si un SKU cambia de rack o se corrige la Etiqueta_base.
        cur.execute("DELETE FROM fact_pasillo_rack_anio WHERE anio=%s AND cod_tienda = ANY(%s)",
                    (anio_actual, tiendas_df))
        pr = (df[df["anio"] == anio_actual]
              .groupby(["cod_tienda", "anio", "pasillo", "rack"], as_index=False)
              .agg(venta=("total", "sum")))
        rows_pr = [(str(r.cod_tienda), int(r.anio), str(r.pasillo), str(r.rack), float(r.venta))
                   for r in pr.itertuples(index=False)]
        if rows_pr:
            psycopg2.extras.execute_values(
                cur, "INSERT INTO fact_pasillo_rack_anio (cod_tienda,anio,pasillo,rack,venta) VALUES %s",
                rows_pr, page_size=5000)

        cur.execute("DELETE FROM fact_producto_anio WHERE anio=%s AND cod_tienda = ANY(%s)",
                    (anio_actual, tiendas_df))
        pa = (df[df["anio"] == anio_actual]
              .groupby(["cod_tienda", "anio", "cod_rapido"], as_index=False)
              .agg(venta=("total", "sum"), cantidad=("cantidad", "sum")))
        rows_pa = [(str(r.cod_tienda), int(r.anio), str(r.cod_rapido), float(r.venta), float(r.cantidad))
                   for r in pa.itertuples(index=False)]
        if rows_pa:
            psycopg2.extras.execute_values(
                cur, "INSERT INTO fact_producto_anio (cod_tienda,anio,cod_rapido,venta,cantidad) VALUES %s",
                rows_pa, page_size=5000)

    print("Agregados YTD de rack/producto actualizados.")

    # --- dim_producto_tienda ---
    dim = (df.sort_values("fecha_emision")
             .groupby(["cod_tienda", "cod_rapido"], as_index=False)
             .agg(descripcion=("descripcion", "last"), marca=("marca", "last"), stock=("stock", "last"),
                  familia=("familia", "last"), subfamilia=("subfamilia", "last"), categoria=("categoria", "last"),
                  clasificacion=("clasificacion", "last"), maneja_stock=("maneja_stock", "last"),
                  zona_pck=("zona_pck", "last"), responsable_linea=("responsable_linea", "last")))
    dim["stock"] = pd.to_numeric(dim["stock"], errors="coerce")
    dim.loc[(dim["stock"] < 0) | (dim["stock"] > 1_000_000), "stock"] = None
    rows_d = [tuple(None if pd.isna(v) else v for v in r)
              for r in dim.itertuples(index=False, name=None)]
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO dim_producto_tienda (cod_tienda,cod_rapido,descripcion,marca,stock,familia,subfamilia,"
        "categoria,clasificacion,maneja_stock,zona_pck,responsable_linea) VALUES %s "
        "ON CONFLICT (cod_tienda,cod_rapido) DO UPDATE SET "
        "descripcion=EXCLUDED.descripcion, marca=EXCLUDED.marca, stock=EXCLUDED.stock, "
        "familia=EXCLUDED.familia, subfamilia=EXCLUDED.subfamilia, categoria=EXCLUDED.categoria, "
        "clasificacion=EXCLUDED.clasificacion, maneja_stock=EXCLUDED.maneja_stock, "
        "zona_pck=EXCLUDED.zona_pck, responsable_linea=EXCLUDED.responsable_linea, actualizado=now()",
        rows_d, page_size=5000)
    print(f"dim_producto_tienda: {len(rows_d):,} filas")

    # --- fact_comparativo_anio (trx, clientes, venta por tienda+anio) ---
    comp = df.groupby(["cod_tienda", "anio"], as_index=False).agg(
        venta=("total", "sum"), trx=("nro_impreso", "nunique"), clientes=("cod_entidad", "nunique"))
    rows_c = [(str(r.cod_tienda), int(r.anio), float(r.venta), int(r.trx), int(r.clientes))
              for r in comp.itertuples(index=False)]
    psycopg2.extras.execute_values(
        cur, "INSERT INTO fact_comparativo_anio (cod_tienda,anio,venta,trx,clientes) VALUES %s "
             "ON CONFLICT (cod_tienda,anio) DO UPDATE SET venta=EXCLUDED.venta, trx=EXCLUDED.trx, "
             "clientes=EXCLUDED.clientes",
        rows_c, page_size=1000)
    print(f"fact_comparativo_anio: {len(rows_c):,} filas")

    # --- fact_cross_sell (se recalcula completa cada corrida) ---
    cs = calcular_cross_sell(df)
    cur.execute("DELETE FROM fact_cross_sell")
    if len(cs):
        rows_cs = list(cs.itertuples(index=False, name=None))
        psycopg2.extras.execute_values(
            cur, "INSERT INTO fact_cross_sell (cod_tienda,sku_a,sku_b,desc_a,desc_b,boletas,soporte,"
                 "confianza_a_b,confianza_b_a,lift) VALUES %s", rows_cs, page_size=5000)
    print(f"fact_cross_sell: {len(cs):,} filas")

    cur.execute("INSERT INTO sync_log (filas_venta, ok, mensaje) VALUES (%s,%s,%s)", (len(rows), True, "OK"))
    conn.commit()
    cur.close()
    conn.close()
    print(f"[{datetime.datetime.now()}] Sincronización completa.")


if __name__ == "__main__":
    main()
