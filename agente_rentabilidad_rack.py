"""
Agente de sincronización V3 — SQL Server -> Postgres (Railway)

Objetivo:
- refrescar venta de año actual y año anterior al MISMO CORTE (hasta ayer),
- mantener stock/atributos/ubicación actual de TODOS los SKU del stock, incluso
  si no vendieron,
- evitar duplicados obsoletos cuando un SKU cambia de rack,
- recalcular agregados, comparativo y cross-sell.

Se ejecuta en un PC/servidor con acceso a SQL Server. Railway no necesita
acceso a la red interna: el agente empuja la información a Postgres.

Variables de entorno:
  SQLSERVER_DSN  cadena ODBC a SQL Server
  DATABASE_URL   conexión Postgres de Railway
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
STORE_SQL = ','.join(f"'{c}'" for c in STORE_CODES)

STORE_CASE = """
CASE s.cod_emp
    WHEN 'VESPUCIO' THEN 'VESPU' WHEN 'SERENA' THEN 'SEREN'
    WHEN 'MAIPU' THEN 'MAIPU' WHEN 'TEMUCO' THEN 'TEMUC'
    WHEN 'RENACA' THEN 'RENAC' WHEN 'CONCE' THEN 'CONCE'
    WHEN 'VALPO' THEN 'VALPA' WHEN 'MADERA' THEN 'SANRO'
    WHEN 'TALCA' THEN 'TALCA' WHEN 'RANCAGUA' THEN 'RANCA'
    WHEN 'MADIMP' THEN 'MAPOC' WHEN 'FONTOVA' THEN 'HUECH'
    WHEN 'PTOMONTT' THEN 'PMONT' ELSE s.cod_emp
END
"""

# Stock: ventana de 7 días para tolerar una carga diaria atrasada; se toma la
# fila más reciente por SKU/tienda dentro de esa ventana.
QUERY_STOCK = f"""
WITH StockRank AS (
    SELECT
        s.Cod_Rapido AS cod_rapido,
        {STORE_CASE} AS cod_tienda,
        s.Etiqueta_Exhibicion AS etiqueta_exhibicion,
        s.Etiqueta AS etiqueta,
        s.Zona_pck AS zona_pck,
        s.ManejaStock AS maneja_stock,
        s.Marca AS marca,
        s.Stock_Disponible AS stock,
        s.SuperFamilia AS familia,
        s.Familia AS subfamilia,
        s.Subfamilia AS categoria,
        s.Clasificacion AS clasificacion,
        s.Responsable_Linea AS responsable_linea,
        ROW_NUMBER() OVER (
            PARTITION BY s.Cod_Rapido, s.cod_emp
            ORDER BY s.Fecha_Proceso DESC
        ) AS rn
    FROM SAV.dbo.SAV_CI_INFSTOCKBODEGA_DIARIO s WITH (NOLOCK)
    WHERE CAST(s.Fecha_Proceso AS date) >= CAST(DATEADD(DAY, -7, GETDATE()) AS date)
)
SELECT cod_tienda, cod_rapido, etiqueta_exhibicion, etiqueta, zona_pck,
       maneja_stock, marca, stock, familia, subfamilia, categoria,
       clasificacion, responsable_linea
FROM StockRank
WHERE rn = 1 AND cod_tienda IN ({STORE_SQL});
"""

# Venta: año actual y año anterior exactamente al mismo corte. Usamos ayer para
# no comparar un día actual todavía incompleto contra un día histórico completo.
QUERY_VENTAS = f"""
DECLARE @CorteActual date = DATEADD(DAY, -1, CAST(GETDATE() AS date));
DECLARE @IniActual date = DATEFROMPARTS(YEAR(@CorteActual), 1, 1);
DECLARE @CorteAnterior date = DATEADD(YEAR, -1, @CorteActual);
DECLARE @IniAnterior date = DATEFROMPARTS(YEAR(@CorteAnterior), 1, 1);

WITH VentasDetalladas AS (
    SELECT RTRIM(LTRIM(f.cod_tienda)) AS cod_tienda,
           d.Cod_rapido AS cod_rapido, d.Descripcion AS descripcion,
           d.Cantidad AS cantidad, d.total AS total, f.Fecha_Emision AS fecha_emision,
           f.nro_impreso, f.cod_entidad,
           CONCAT('F|', RTRIM(LTRIM(f.Cod_Emp)), '|', f.Tipo_Documento, '|', f.Nro_Interno) AS transaccion_id
    FROM SAV_VT.dbo.SAV_VT_FCVCab f WITH (NOLOCK)
    INNER JOIN SAV_VT.dbo.SAV_VT_FCVdet d WITH (NOLOCK)
      ON f.Tipo_Documento=d.Tipo_Documento AND f.Nro_Interno=d.Nro_Interno AND f.Cod_Emp=d.Cod_Emp
    WHERE RTRIM(LTRIM(f.cod_tienda)) IN ({STORE_SQL})
      AND ((f.Fecha_Emision >= @IniActual AND f.Fecha_Emision < DATEADD(DAY,1,@CorteActual))
        OR (f.Fecha_Emision >= @IniAnterior AND f.Fecha_Emision < DATEADD(DAY,1,@CorteAnterior)))

    UNION ALL

    SELECT RTRIM(LTRIM(b.cod_tienda)) AS cod_tienda,
           e.Cod_rapido AS cod_rapido, e.Descripcion AS descripcion,
           e.Cantidad AS cantidad, e.total AS total, b.Fecha_Emision AS fecha_emision,
           b.nro_impreso, b.cod_entidad,
           CONCAT('B|', RTRIM(LTRIM(b.Cod_Emp)), '|', b.Tipo_Documento, '|', b.Nro_Interno) AS transaccion_id
    FROM SAV_VT.dbo.SAV_VT_BLVcab b WITH (NOLOCK)
    INNER JOIN SAV_VT.dbo.SAV_VT_BLVdet e WITH (NOLOCK)
      ON b.Tipo_Documento=e.Tipo_Documento AND b.Nro_Interno=e.Nro_Interno AND b.Cod_Emp=e.Cod_Emp
    WHERE RTRIM(LTRIM(b.cod_tienda)) IN ({STORE_SQL})
      AND ((b.Fecha_Emision >= @IniActual AND b.Fecha_Emision < DATEADD(DAY,1,@CorteActual))
        OR (b.Fecha_Emision >= @IniAnterior AND b.Fecha_Emision < DATEADD(DAY,1,@CorteAnterior)))
)
SELECT * FROM VentasDetalladas;
"""


def calcular_pasillo_rack(df):
    etq = df["etiqueta"].fillna("").astype(str).str.strip()
    exh = df["etiqueta_exhibicion"].fillna("").astype(str).str.strip()
    usa_exh = df["zona_pck"].isin(["Z03", "Z04", "Z06"]) | (df["maneja_stock"] == "N")
    base = pd.Series("", index=df.index, dtype=object)
    base[usa_exh] = exh[usa_exh].where(exh[usa_exh] != "", etq[usa_exh])
    base[~usa_exh] = etq[~usa_exh].where(etq[~usa_exh] != "", exh[~usa_exh])
    out = df.copy()
    out["pasillo"] = base.str.slice(0, 3)
    out["rack"] = base.str.slice(0, 8)
    return out


def calcular_cross_sell(df):
    """Soporte/confianza/lift por transacción real del año actual.

    Usa ``transaccion_id`` (tipo + empresa + documento + interno), no solo el
    número impreso, para evitar unir por accidente dos documentos distintos.
    """
    filas = []
    for tienda, g in df.groupby("cod_tienda"):
        baskets = collections.defaultdict(set)
        gx = g[(pd.to_numeric(g["cantidad"], errors="coerce").fillna(0) > 0) &
               (pd.to_numeric(g["total"], errors="coerce").fillna(0) > 0)]
        for trx, sku in zip(gx["transaccion_id"], gx["cod_rapido"]):
            if pd.notna(trx) and pd.notna(sku):
                baskets[str(trx)].add(str(sku))
        total = len(baskets)
        if not total:
            continue
        sku_count = collections.Counter()
        pair_count = collections.Counter()
        for skus in baskets.values():
            for s in skus:
                sku_count[s] += 1
            if 2 <= len(skus) <= 20:
                for a, b in itertools.combinations(sorted(skus), 2):
                    pair_count[(a, b)] += 1
        desc_df = g[["cod_rapido", "descripcion"]].copy()
        desc_df["cod_rapido"] = desc_df["cod_rapido"].astype(str)
        desc = desc_df.drop_duplicates("cod_rapido").set_index("cod_rapido")["descripcion"]
        for (a, b), n_ab in sorted(pair_count.items(), key=lambda kv: kv[1], reverse=True)[:500]:
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


def _safe(v):
    return None if pd.isna(v) else v


def main():
    if not DATABASE_URL:
        print("Falta DATABASE_URL")
        sys.exit(1)

    print(f"[{datetime.datetime.now()}] Leyendo SQL Server...")
    with pyodbc.connect(SQLSERVER_DSN) as cn:
        stock = pd.read_sql(QUERY_STOCK, cn)
        ventas = pd.read_sql(QUERY_VENTAS, cn)

    if stock.empty:
        raise RuntimeError("La consulta de stock devolvió 0 filas. Se cancela para no borrar stock vigente.")
    if len(stock) < 1000:
        raise RuntimeError(f"Stock sospechosamente pequeño ({len(stock):,} filas). Se cancela por seguridad.")
    if ventas.empty:
        raise RuntimeError("La consulta de ventas devolvió 0 filas. Se cancela la sincronización.")

    stock["cod_tienda"] = stock["cod_tienda"].astype(str).str.strip()
    stock["cod_rapido"] = stock["cod_rapido"].astype(str).str.strip()
    stock = calcular_pasillo_rack(stock)
    stock["stock"] = pd.to_numeric(stock["stock"], errors="coerce")
    stock.loc[(stock["stock"] < 0) | (stock["stock"] > 1_000_000), "stock"] = None

    ventas["cod_tienda"] = ventas["cod_tienda"].astype(str).str.strip()
    ventas["cod_rapido"] = ventas["cod_rapido"].astype(str).str.strip()
    ventas["fecha_emision"] = pd.to_datetime(ventas["fecha_emision"])
    ventas["anio"] = ventas["fecha_emision"].dt.year.astype(int)
    ventas["mes"] = ventas["fecha_emision"].dt.month.astype(int)
    ventas["semana"] = ventas["fecha_emision"].dt.isocalendar().week.astype(int)
    ventas["total"] = pd.to_numeric(ventas["total"], errors="coerce").fillna(0)
    ventas["cantidad"] = pd.to_numeric(ventas["cantidad"], errors="coerce").fillna(0)

    attrs = stock[["cod_tienda", "cod_rapido", "pasillo", "rack"]].drop_duplicates(["cod_tienda", "cod_rapido"])
    venta_rack = ventas.merge(attrs, on=["cod_tienda", "cod_rapido"], how="left")
    sin_ubic = venta_rack["pasillo"].isna() | (venta_rack["pasillo"].fillna("").astype(str).str.strip() == "")
    venta_rack = venta_rack[~sin_ubic].copy()

    cobertura = float(venta_rack["total"].sum()) / float(ventas["total"].sum() or 1)
    print(f"Stock: {len(stock):,} SKU-tienda | ventas: {len(ventas):,} líneas | cobertura ubicación por $: {cobertura:.1%}")

    anios = sorted(ventas["anio"].unique().tolist())
    tiendas = sorted(set(STORE_CODES) & set(ventas["cod_tienda"].unique()))
    anio_actual = max(anios)

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        # Compatibilidad: el agente puede correr incluso antes de que la nueva
        # versión de la web haya ejecutado schema.sql por primera vez.
        cur.execute("ALTER TABLE dim_producto_tienda ADD COLUMN IF NOT EXISTS pasillo VARCHAR(20)")
        cur.execute("ALTER TABLE dim_producto_tienda ADD COLUMN IF NOT EXISTS rack VARCHAR(20)")

        # -------- Dimensión: stock actual completo, incluso sin venta --------
        desc = (ventas.sort_values("fecha_emision")
                .groupby(["cod_tienda", "cod_rapido"], as_index=False)
                .agg(descripcion=("descripcion", "last")))
        dim = stock.merge(desc, on=["cod_tienda", "cod_rapido"], how="left")

        # Evita que un SKU desaparecido del snapshot siga aparentando stock positivo.
        cur.execute("UPDATE dim_producto_tienda SET stock=0, actualizado=now() WHERE cod_tienda = ANY(%s)", (tiendas,))
        rows_dim = [(
            str(r.cod_tienda), str(r.cod_rapido), _safe(r.descripcion), _safe(r.marca), _safe(r.stock),
            _safe(r.familia), _safe(r.subfamilia), _safe(r.categoria), _safe(r.clasificacion),
            _safe(r.maneja_stock), _safe(r.zona_pck), _safe(r.responsable_linea),
            _safe(r.pasillo), _safe(r.rack)
        ) for r in dim.itertuples(index=False)]
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO dim_producto_tienda (cod_tienda,cod_rapido,descripcion,marca,stock,familia,subfamilia,"
            "categoria,clasificacion,maneja_stock,zona_pck,responsable_linea,pasillo,rack) VALUES %s "
            "ON CONFLICT (cod_tienda,cod_rapido) DO UPDATE SET "
            "descripcion=COALESCE(EXCLUDED.descripcion,dim_producto_tienda.descripcion), marca=EXCLUDED.marca, "
            "stock=EXCLUDED.stock, familia=EXCLUDED.familia, subfamilia=EXCLUDED.subfamilia, "
            "categoria=EXCLUDED.categoria, clasificacion=EXCLUDED.clasificacion, maneja_stock=EXCLUDED.maneja_stock, "
            "zona_pck=EXCLUDED.zona_pck, responsable_linea=EXCLUDED.responsable_linea, "
            "pasillo=EXCLUDED.pasillo, rack=EXCLUDED.rack, actualizado=now()",
            rows_dim, page_size=5000)
        print(f"dim_producto_tienda: {len(rows_dim):,} filas")

        # -------- Hecho semanal: snapshot completo de los 2 años --------
        agg = (venta_rack.groupby(["cod_tienda", "anio", "mes", "semana", "pasillo", "rack", "cod_rapido"], as_index=False)
               .agg(venta=("total", "sum"), cantidad=("cantidad", "sum")))
        rows_fact = [(str(r.cod_tienda), int(r.anio), int(r.mes), int(r.semana), str(r.pasillo), str(r.rack),
                      str(r.cod_rapido), float(r.venta), 0.0, float(r.cantidad))
                     for r in agg.itertuples(index=False)]
        cur.execute("DELETE FROM fact_venta_semana WHERE cod_tienda = ANY(%s) AND anio = ANY(%s)", (tiendas, anios))
        if rows_fact:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO fact_venta_semana (cod_tienda,anio,mes,semana,pasillo,rack,cod_rapido,venta,margen,cantidad) VALUES %s",
                rows_fact, page_size=5000)
        print(f"fact_venta_semana: {len(rows_fact):,} filas")

        # -------- Agregados anuales --------
        cur.execute("DELETE FROM fact_pasillo_rack_anio WHERE cod_tienda = ANY(%s) AND anio = ANY(%s)", (tiendas, anios))
        pr = (venta_rack.groupby(["cod_tienda", "anio", "pasillo", "rack"], as_index=False)
              .agg(venta=("total", "sum")))
        rows_pr = [(str(r.cod_tienda), int(r.anio), str(r.pasillo), str(r.rack), float(r.venta))
                   for r in pr.itertuples(index=False)]
        if rows_pr:
            psycopg2.extras.execute_values(cur,
                "INSERT INTO fact_pasillo_rack_anio (cod_tienda,anio,pasillo,rack,venta) VALUES %s",
                rows_pr, page_size=5000)

        cur.execute("DELETE FROM fact_producto_anio WHERE cod_tienda = ANY(%s) AND anio = ANY(%s)", (tiendas, anios))
        pa = (ventas.groupby(["cod_tienda", "anio", "cod_rapido"], as_index=False)
              .agg(venta=("total", "sum"), cantidad=("cantidad", "sum")))
        rows_pa = [(str(r.cod_tienda), int(r.anio), str(r.cod_rapido), float(r.venta), float(r.cantidad))
                   for r in pa.itertuples(index=False)]
        if rows_pa:
            psycopg2.extras.execute_values(cur,
                "INSERT INTO fact_producto_anio (cod_tienda,anio,cod_rapido,venta,cantidad) VALUES %s",
                rows_pa, page_size=5000)

        # -------- Comparativo mismo corte --------
        comp = ventas.groupby(["cod_tienda", "anio"], as_index=False).agg(
            venta=("total", "sum"), trx=("transaccion_id", "nunique"), clientes=("cod_entidad", "nunique"))
        rows_comp = [(str(r.cod_tienda), int(r.anio), float(r.venta), int(r.trx), int(r.clientes))
                     for r in comp.itertuples(index=False)]
        if rows_comp:
            psycopg2.extras.execute_values(
                cur, "INSERT INTO fact_comparativo_anio (cod_tienda,anio,venta,trx,clientes) VALUES %s "
                     "ON CONFLICT (cod_tienda,anio) DO UPDATE SET venta=EXCLUDED.venta,trx=EXCLUDED.trx,clientes=EXCLUDED.clientes",
                rows_comp, page_size=1000)

        # -------- Cross-sell solo del año actual --------
        cs = calcular_cross_sell(ventas[ventas["anio"] == anio_actual].copy())
        cur.execute("DELETE FROM fact_cross_sell")
        if not cs.empty:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO fact_cross_sell (cod_tienda,sku_a,sku_b,desc_a,desc_b,boletas,soporte,confianza_a_b,confianza_b_a,lift) VALUES %s",
                list(cs.itertuples(index=False, name=None)), page_size=5000)

        mensaje = f"OK | años {','.join(map(str, anios))} | cobertura ubicación {cobertura:.1%}"
        cur.execute("INSERT INTO sync_log (filas_venta, ok, mensaje) VALUES (%s,%s,%s)",
                    (len(rows_fact), True, mensaje))
        conn.commit()
        print(f"[{datetime.datetime.now()}] Sincronización completa. {mensaje}")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
