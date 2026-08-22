"""
Agente de sincronización V8 — SQL Server -> Postgres (Railway)

Objetivo:
- refrescar venta de año actual y año anterior al MISMO CORTE (hasta ayer),
- mantener stock/atributos/ubicación actual de TODOS los SKU del stock, incluso
  si no vendieron,
- evitar duplicados obsoletos cuando un SKU cambia de rack,
- reconstruir la ubicación FÍSICA real para la ventana que aún existe en INFSTOCK (aprox. 3 meses),
- guardar historial permanente de ubicación SKU -> rack,
- calcular venta neta sin IVA (FCV/BLV /1.19 + NCV negativa),
- recalcular agregados, comparativo y cross-sell (cross-sell solo FCV/BLV).

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
        s.Fecha_Proceso AS fecha_proceso,
        CAST(NULL AS varchar(200)) AS responsable_linea,  -- columna no existe en INFSTOCK de tu ambiente
        ROW_NUMBER() OVER (
            PARTITION BY s.Cod_Rapido, s.cod_emp
            ORDER BY s.Fecha_Proceso DESC
        ) AS rn
    FROM SAV.dbo.SAV_CI_INFSTOCKBODEGA_DIARIO s WITH (NOLOCK)
    WHERE s.Fecha_Proceso >= DATEADD(DAY, -7, CAST(GETDATE() AS date))
)
SELECT cod_tienda, cod_rapido, etiqueta_exhibicion, etiqueta, zona_pck,
       maneja_stock, marca, stock, familia, subfamilia, categoria,
       clasificacion, fecha_proceso, responsable_linea
FROM StockRank
WHERE rn = 1 AND cod_tienda IN ({STORE_SQL});
"""



# Historial de ubicación física. INFSTOCK se recorta y normalmente conserva
# alrededor de 3 meses, por eso pedimos 100 días y comprimimos en SQL solo los
# días donde cambia la etiqueta. Así no transferimos millones de snapshots diarios.
QUERY_UBIC_HIST = f"""
DECLARE @IniHist date = DATEADD(DAY, -100, CAST(GETDATE() AS date));

WITH Base AS (
    SELECT
        CAST(s.Fecha_Proceso AS date) AS fecha_ubicacion,
        s.Cod_Rapido AS cod_rapido,
        {STORE_CASE} AS cod_tienda,
        s.Etiqueta_Exhibicion AS etiqueta_exhibicion,
        s.Etiqueta AS etiqueta,
        s.Zona_pck AS zona_pck,
        s.ManejaStock AS maneja_stock,
        ROW_NUMBER() OVER (
            PARTITION BY s.cod_emp, s.Cod_Rapido, CAST(s.Fecha_Proceso AS date)
            ORDER BY s.Fecha_Proceso DESC
        ) AS rn
    FROM SAV.dbo.SAV_CI_INFSTOCKBODEGA_DIARIO s WITH (NOLOCK)
    WHERE s.Fecha_Proceso >= @IniHist
),
Daily AS (
    SELECT fecha_ubicacion, cod_rapido, cod_tienda,
           CASE
             WHEN zona_pck IN ('Z03','Z04','Z06') OR maneja_stock = 'N'
               THEN COALESCE(NULLIF(LTRIM(RTRIM(etiqueta_exhibicion)),''), LTRIM(RTRIM(etiqueta)))
             ELSE COALESCE(NULLIF(LTRIM(RTRIM(etiqueta)),''), LTRIM(RTRIM(etiqueta_exhibicion)))
           END AS etiqueta_base
    FROM Base
    WHERE rn = 1 AND cod_tienda IN ({STORE_SQL})
),
Cambios AS (
    SELECT *,
           LAG(ISNULL(etiqueta_base,'')) OVER (
               PARTITION BY cod_tienda, cod_rapido ORDER BY fecha_ubicacion
           ) AS etiqueta_previa
    FROM Daily
)
SELECT fecha_ubicacion, cod_tienda, cod_rapido,
       LEFT(ISNULL(etiqueta_base,''),3) AS pasillo,
       LEFT(ISNULL(etiqueta_base,''),8) AS rack
FROM Cambios
WHERE etiqueta_previa IS NULL OR etiqueta_previa <> ISNULL(etiqueta_base,'')
ORDER BY fecha_ubicacion, cod_tienda, cod_rapido;
"""

# Venta neta: FCV/BLV se llevan a neto sin IVA (/1.19) y las NCV se
# incorporan con signo negativo. La NCV se imputa al SKU/documento original.
# Para ubicación física de una NCV usamos la fecha del documento original;
# para el período contable conservamos la fecha de emisión de la NCV.
QUERY_VENTAS = f"""
DECLARE @CorteActual date = DATEADD(DAY, -1, CAST(GETDATE() AS date));
DECLARE @IniActual date = DATEFROMPARTS(YEAR(@CorteActual), 1, 1);
DECLARE @CorteAnterior date = DATEADD(YEAR, -1, @CorteActual);
DECLARE @IniAnterior date = DATEFROMPARTS(YEAR(@CorteAnterior), 1, 1);
DECLARE @IniOrigen date = DATEADD(YEAR, -1, @IniAnterior);

WITH Positivas AS (
    SELECT RTRIM(LTRIM(f.cod_tienda)) AS cod_tienda,
           CAST(d.Cod_rapido AS varchar(30)) AS cod_rapido,
           d.Descripcion AS descripcion,
           CAST(d.Cantidad AS decimal(18,4)) AS cantidad,
           CAST(d.total / 1.19 AS decimal(18,4)) AS total,
           CAST(d.total / 1.19 AS decimal(18,4)) AS venta_bruta,
           CAST(0 AS decimal(18,4)) AS ncv,
           CAST(d.Cantidad AS decimal(18,4)) AS cantidad_bruta,
           CAST(0 AS decimal(18,4)) AS cantidad_ncv,
           f.Fecha_Emision AS fecha_emision,
           f.Fecha_Emision AS fecha_ubicacion_ref,
           CAST(f.nro_impreso AS varchar(100)) AS nro_impreso,
           CAST(f.cod_entidad AS varchar(50)) AS cod_entidad,
           CONCAT('F|', RTRIM(LTRIM(f.Cod_Emp)), '|', f.Tipo_Documento, '|', f.Nro_Interno) AS transaccion_id,
           'FCV' AS origen
    FROM SAV_VT.dbo.SAV_VT_FCVCab f WITH (NOLOCK)
    INNER JOIN SAV_VT.dbo.SAV_VT_FCVdet d WITH (NOLOCK)
      ON f.Tipo_Documento=d.Tipo_Documento AND f.Nro_Interno=d.Nro_Interno AND f.Cod_Emp=d.Cod_Emp
    WHERE RTRIM(LTRIM(f.cod_tienda)) IN ({STORE_SQL})
      AND RTRIM(LTRIM(f.cod_tienda)) <> 'ADMIN'
      AND ((f.Fecha_Emision >= @IniActual AND f.Fecha_Emision < DATEADD(DAY,1,@CorteActual))
        OR (f.Fecha_Emision >= @IniAnterior AND f.Fecha_Emision < DATEADD(DAY,1,@CorteAnterior)))

    UNION ALL

    SELECT RTRIM(LTRIM(b.cod_tienda)) AS cod_tienda,
           CAST(e.Cod_rapido AS varchar(30)) AS cod_rapido,
           e.Descripcion AS descripcion,
           CAST(e.Cantidad AS decimal(18,4)) AS cantidad,
           CAST(e.total / 1.19 AS decimal(18,4)) AS total,
           CAST(e.total / 1.19 AS decimal(18,4)) AS venta_bruta,
           CAST(0 AS decimal(18,4)) AS ncv,
           CAST(e.Cantidad AS decimal(18,4)) AS cantidad_bruta,
           CAST(0 AS decimal(18,4)) AS cantidad_ncv,
           b.Fecha_Emision AS fecha_emision,
           b.Fecha_Emision AS fecha_ubicacion_ref,
           CAST(b.nro_impreso AS varchar(100)) AS nro_impreso,
           CAST(b.cod_entidad AS varchar(50)) AS cod_entidad,
           CONCAT('B|', RTRIM(LTRIM(b.Cod_Emp)), '|', b.Tipo_Documento, '|', b.Nro_Interno) AS transaccion_id,
           'BLV' AS origen
    FROM SAV_VT.dbo.SAV_VT_BLVcab b WITH (NOLOCK)
    INNER JOIN SAV_VT.dbo.SAV_VT_BLVdet e WITH (NOLOCK)
      ON b.Tipo_Documento=e.Tipo_Documento AND b.Nro_Interno=e.Nro_Interno AND b.Cod_Emp=e.Cod_Emp
    WHERE RTRIM(LTRIM(b.cod_tienda)) IN ({STORE_SQL})
      AND RTRIM(LTRIM(b.cod_tienda)) <> 'ADMIN'
      AND ((b.Fecha_Emision >= @IniActual AND b.Fecha_Emision < DATEADD(DAY,1,@CorteActual))
        OR (b.Fecha_Emision >= @IniAnterior AND b.Fecha_Emision < DATEADD(DAY,1,@CorteAnterior)))
),
Origenes AS (
    SELECT f.Cod_Emp, f.Tipo_Documento, f.Nro_Interno, d.Nro_Linea,
           CAST(d.Cod_Rapido AS varchar(30)) AS Cod_Rapido,
           d.Descripcion, RTRIM(LTRIM(f.Cod_Tienda)) AS Cod_Tienda,
           f.Fecha_Emision, CAST(f.Nro_Impreso AS varchar(100)) AS Nro_Impreso
    FROM SAV_VT.dbo.SAV_VT_FCVCab f WITH (NOLOCK)
    INNER JOIN SAV_VT.dbo.SAV_VT_FCVdet d WITH (NOLOCK)
      ON f.Tipo_Documento=d.Tipo_Documento AND f.Nro_Interno=d.Nro_Interno AND f.Cod_Emp=d.Cod_Emp
    WHERE f.Fecha_Emision >= @IniOrigen AND f.Fecha_Emision < DATEADD(DAY,1,@CorteActual)

    UNION ALL

    SELECT b.Cod_Emp, b.Tipo_Documento, b.Nro_Interno, e.Nro_Linea,
           CAST(e.Cod_Rapido AS varchar(30)) AS Cod_Rapido,
           e.Descripcion, RTRIM(LTRIM(b.Cod_Tienda)) AS Cod_Tienda,
           b.Fecha_Emision, CAST(b.Nro_Impreso AS varchar(100)) AS Nro_Impreso
    FROM SAV_VT.dbo.SAV_VT_BLVcab b WITH (NOLOCK)
    INNER JOIN SAV_VT.dbo.SAV_VT_BLVdet e WITH (NOLOCK)
      ON b.Tipo_Documento=e.Tipo_Documento AND b.Nro_Interno=e.Nro_Interno AND b.Cod_Emp=e.Cod_Emp
    WHERE b.Fecha_Emision >= @IniOrigen AND b.Fecha_Emision < DATEADD(DAY,1,@CorteActual)
),
Notas AS (
    SELECT RTRIM(LTRIM(a.Cod_Tienda)) AS cod_tienda,
           CAST(COALESCE(o.Cod_Rapido, b.Cod_Rapido) AS varchar(30)) AS cod_rapido,
           o.Descripcion AS descripcion,
           CAST(b.Cantidad AS decimal(18,4)) * -1 AS cantidad,
           CAST(b.Total / -1.19 AS decimal(18,4)) AS total,
           CAST(0 AS decimal(18,4)) AS venta_bruta,
           CAST(b.Total / -1.19 AS decimal(18,4)) AS ncv,
           CAST(0 AS decimal(18,4)) AS cantidad_bruta,
           CAST(b.Cantidad AS decimal(18,4)) * -1 AS cantidad_ncv,
           a.Fecha_Emision AS fecha_emision,
           COALESCE(o.Fecha_Emision, a.Fecha_Emision) AS fecha_ubicacion_ref,
           CAST(o.Nro_Impreso AS varchar(100)) AS nro_impreso,
           CAST(a.Cod_Entidad AS varchar(50)) AS cod_entidad,
           CONCAT('N|', RTRIM(LTRIM(a.Cod_Emp)), '|', a.Nro_Interno, '|', b.Nro_Linea) AS transaccion_id,
           'NCV' AS origen
    FROM SAV_VT.dbo.SAV_VT_NCVcab a WITH (NOLOCK)
    INNER JOIN SAV_VT.dbo.SAV_VT_NCVdet b WITH (NOLOCK)
      ON a.Cod_Emp=b.Cod_Emp AND a.Nro_Interno=b.Nro_Interno
    INNER JOIN Origenes o
      ON o.Nro_Interno=b.Nro_InternoP
     AND o.Tipo_Documento=b.Tipo_DocumentoP
     AND o.Nro_Linea=b.Nro_LineaP
    WHERE RTRIM(LTRIM(a.Cod_Tienda)) IN ({STORE_SQL})
      AND RTRIM(LTRIM(a.Cod_Tienda)) <> 'ADMIN'
      AND RTRIM(LTRIM(o.Cod_Tienda)) <> 'ADMIN'
      AND ((a.Fecha_Emision >= @IniActual AND a.Fecha_Emision < DATEADD(DAY,1,@CorteActual))
        OR (a.Fecha_Emision >= @IniAnterior AND a.Fecha_Emision < DATEADD(DAY,1,@CorteAnterior)))
)
SELECT * FROM Positivas
UNION ALL
SELECT * FROM Notas;
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


def _fetch_df(cur, query, params=None, columns=None):
    cur.execute(query, params or ())
    rows = cur.fetchall()
    cols = columns or [d.name if hasattr(d, "name") else d[0] for d in cur.description]
    return pd.DataFrame(rows, columns=cols)


def _compactar_historial(saved, current):
    cols = ["cod_tienda", "cod_rapido", "fecha_desde", "pasillo", "rack"]
    piezas = []
    if saved is not None and not saved.empty:
        s = saved[cols].copy()
        s["_fuente"] = 0
        piezas.append(s)
    if current is not None and not current.empty:
        c = current[["cod_tienda", "cod_rapido", "fecha_ubicacion", "pasillo", "rack"]].copy()
        c = c.rename(columns={"fecha_ubicacion": "fecha_desde"})
        c["_fuente"] = 1
        piezas.append(c)
    if not piezas:
        return pd.DataFrame(columns=cols)
    h = pd.concat(piezas, ignore_index=True)
    h["fecha_desde"] = pd.to_datetime(h["fecha_desde"]).dt.normalize()
    h["pasillo"] = h["pasillo"].fillna("").astype(str).str.strip()
    h["rack"] = h["rack"].fillna("").astype(str).str.strip()
    h = h.sort_values(["cod_tienda", "cod_rapido", "fecha_desde", "_fuente"])
    h = h.drop_duplicates(["cod_tienda", "cod_rapido", "fecha_desde"], keep="last")
    prev_p = h.groupby(["cod_tienda", "cod_rapido"])["pasillo"].shift()
    prev_r = h.groupby(["cod_tienda", "cod_rapido"])["rack"].shift()
    keep = prev_p.isna() | (h["pasillo"] != prev_p) | (h["rack"] != prev_r)
    return h.loc[keep, cols].reset_index(drop=True)


def main():
    if not DATABASE_URL:
        print("Falta DATABASE_URL")
        sys.exit(1)

    print(f"[{datetime.datetime.now()}] Leyendo SQL Server...")
    with pyodbc.connect(SQLSERVER_DSN) as cn:
        stock = pd.read_sql(QUERY_STOCK, cn)
        ubic_hist = pd.read_sql(QUERY_UBIC_HIST, cn)
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
    stock["fecha_proceso"] = pd.to_datetime(stock["fecha_proceso"], errors="coerce")

    ventas["cod_tienda"] = ventas["cod_tienda"].astype(str).str.strip()
    ventas["cod_rapido"] = ventas["cod_rapido"].astype(str).str.strip()
    ventas["fecha_emision"] = pd.to_datetime(ventas["fecha_emision"])
    ventas["fecha_ubicacion_ref"] = pd.to_datetime(ventas["fecha_ubicacion_ref"], errors="coerce")
    ventas["fecha_ubicacion_ref"] = ventas["fecha_ubicacion_ref"].fillna(ventas["fecha_emision"])
    ventas["anio"] = ventas["fecha_emision"].dt.year.astype(int)
    ventas["mes"] = ventas["fecha_emision"].dt.month.astype(int)
    ventas["semana"] = ventas["fecha_emision"].dt.isocalendar().week.astype(int)
    for c in ["total", "cantidad", "venta_bruta", "ncv", "cantidad_bruta", "cantidad_ncv"]:
        ventas[c] = pd.to_numeric(ventas[c], errors="coerce").fillna(0)
    ventas["origen"] = ventas["origen"].fillna("").astype(str).str.upper()
    ventas_pos = ventas[ventas["origen"].isin(["FCV", "BLV"])].copy()
    ncv_df = ventas[ventas["origen"] == "NCV"].copy()
    bruto_sql = float(ventas["venta_bruta"].sum() or 0)
    ncv_sql = float(ncv_df["ncv"].sum() or 0)
    neto_sql = float(ventas["total"].sum() or 0)
    print(f"Ventas SQL: {len(ventas_pos):,} líneas FCV/BLV + {len(ncv_df):,} líneas NCV | "
          f"bruta s/IVA ${bruto_sql:,.0f} | NCV ${ncv_sql:,.0f} | neta ${neto_sql:,.0f}")

    if ubic_hist is not None and not ubic_hist.empty:
        ubic_hist["cod_tienda"] = ubic_hist["cod_tienda"].astype(str).str.strip()
        ubic_hist["cod_rapido"] = ubic_hist["cod_rapido"].astype(str).str.strip()
        ubic_hist["fecha_ubicacion"] = pd.to_datetime(ubic_hist["fecha_ubicacion"]).dt.normalize()
        ubic_hist["pasillo"] = ubic_hist["pasillo"].fillna("").astype(str).str.strip()
        ubic_hist["rack"] = ubic_hist["rack"].fillna("").astype(str).str.strip()

    anios = sorted(ventas["anio"].unique().tolist())
    tiendas = sorted(set(STORE_CODES) & set(ventas["cod_tienda"].unique()))
    anio_actual = max(anios)

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        # Asegura tiendas y esquema V8 aun si la web todavía no se redeployó.
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO dim_tienda (cod_tienda,nombre,tipo) VALUES %s ON CONFLICT (cod_tienda) DO NOTHING",
            [(t, t, None) for t in tiendas], page_size=100,
        )
        cur.execute("ALTER TABLE dim_producto_tienda ADD COLUMN IF NOT EXISTS pasillo VARCHAR(20)")
        cur.execute("ALTER TABLE dim_producto_tienda ADD COLUMN IF NOT EXISTS rack VARCHAR(20)")
        for tabla in ("fact_venta_semana", "fact_venta_rack_dia"):
            # fact_venta_rack_dia puede no existir todavía; se crea abajo antes de sus ALTER.
            if tabla == "fact_venta_semana":
                cur.execute("ALTER TABLE fact_venta_semana ADD COLUMN IF NOT EXISTS venta_bruta NUMERIC(14,2) NOT NULL DEFAULT 0")
                cur.execute("ALTER TABLE fact_venta_semana ADD COLUMN IF NOT EXISTS ncv NUMERIC(14,2) NOT NULL DEFAULT 0")
                cur.execute("ALTER TABLE fact_venta_semana ADD COLUMN IF NOT EXISTS cantidad_bruta NUMERIC(14,2) NOT NULL DEFAULT 0")
                cur.execute("ALTER TABLE fact_venta_semana ADD COLUMN IF NOT EXISTS cantidad_ncv NUMERIC(14,2) NOT NULL DEFAULT 0")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS fact_venta_rack_dia (
                cod_tienda VARCHAR(10) NOT NULL REFERENCES dim_tienda(cod_tienda),
                fecha DATE NOT NULL, anio SMALLINT NOT NULL, mes SMALLINT NOT NULL, semana SMALLINT NOT NULL,
                pasillo VARCHAR(20) NOT NULL, rack VARCHAR(20) NOT NULL DEFAULT '',
                cod_rapido VARCHAR(30) NOT NULL,
                venta NUMERIC(14,2) NOT NULL DEFAULT 0,
                cantidad NUMERIC(14,2) NOT NULL DEFAULT 0,
                venta_bruta NUMERIC(14,2) NOT NULL DEFAULT 0,
                ncv NUMERIC(14,2) NOT NULL DEFAULT 0,
                cantidad_bruta NUMERIC(14,2) NOT NULL DEFAULT 0,
                cantidad_ncv NUMERIC(14,2) NOT NULL DEFAULT 0,
                PRIMARY KEY (cod_tienda, fecha, pasillo, rack, cod_rapido)
            )
        """)
        cur.execute("ALTER TABLE fact_venta_rack_dia ADD COLUMN IF NOT EXISTS venta_bruta NUMERIC(14,2) NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE fact_venta_rack_dia ADD COLUMN IF NOT EXISTS ncv NUMERIC(14,2) NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE fact_venta_rack_dia ADD COLUMN IF NOT EXISTS cantidad_bruta NUMERIC(14,2) NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE fact_venta_rack_dia ADD COLUMN IF NOT EXISTS cantidad_ncv NUMERIC(14,2) NOT NULL DEFAULT 0")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fvrd_tienda_fecha ON fact_venta_rack_dia (cod_tienda, fecha)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fvrd_tienda_rack ON fact_venta_rack_dia (cod_tienda, rack, fecha)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sync_ubicacion_fisica (
                cod_tienda VARCHAR(10) PRIMARY KEY REFERENCES dim_tienda(cod_tienda),
                fecha_desde DATE, fecha_hasta DATE, cobertura_venta_pct NUMERIC(7,4),
                actualizado TIMESTAMP DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hist_ubicacion_sku (
                cod_tienda VARCHAR(10) NOT NULL REFERENCES dim_tienda(cod_tienda),
                cod_rapido VARCHAR(30) NOT NULL,
                fecha_desde DATE NOT NULL,
                pasillo VARCHAR(20) NOT NULL DEFAULT '',
                rack VARCHAR(20) NOT NULL DEFAULT '',
                fuente VARCHAR(20) NOT NULL DEFAULT 'INFSTOCK',
                actualizado TIMESTAMP DEFAULT now(),
                PRIMARY KEY (cod_tienda, cod_rapido, fecha_desde)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_hus_tienda_fecha ON hist_ubicacion_sku (cod_tienda, fecha_desde)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_hus_sku_fecha ON hist_ubicacion_sku (cod_tienda, cod_rapido, fecha_desde)")

        # -------- Historial permanente de ubicación --------
        hist_saved = _fetch_df(
            cur,
            "SELECT cod_tienda,cod_rapido,fecha_desde,pasillo,rack FROM hist_ubicacion_sku WHERE cod_tienda = ANY(%s)",
            (tiendas,),
            ["cod_tienda", "cod_rapido", "fecha_desde", "pasillo", "rack"],
        )
        hist_full = _compactar_historial(hist_saved, ubic_hist)
        fecha_hist_ventana = None
        if ubic_hist is not None and not ubic_hist.empty:
            fecha_hist_ventana = ubic_hist["fecha_ubicacion"].min().date()
            cur.execute("DELETE FROM hist_ubicacion_sku WHERE cod_tienda = ANY(%s) AND fecha_desde >= %s",
                        (tiendas, fecha_hist_ventana))
            persist = hist_full[hist_full["fecha_desde"].dt.date >= fecha_hist_ventana].copy()
            rows_hist = [(str(r.cod_tienda), str(r.cod_rapido), r.fecha_desde.date(), str(r.pasillo), str(r.rack))
                         for r in persist.itertuples(index=False)]
            if rows_hist:
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO hist_ubicacion_sku (cod_tienda,cod_rapido,fecha_desde,pasillo,rack) VALUES %s "
                    "ON CONFLICT (cod_tienda,cod_rapido,fecha_desde) DO UPDATE SET "
                    "pasillo=EXCLUDED.pasillo,rack=EXCLUDED.rack,actualizado=now()",
                    rows_hist, page_size=5000,
                )
        else:
            rows_hist = []

        fecha_hist_min = hist_full["fecha_desde"].min() if not hist_full.empty else None
        # fecha_hasta no es la fecha del último CAMBIO, sino el último snapshot real de INFSTOCK.
        # La ubicación se mantiene vigente entre cambios.
        fecha_hist_max = stock["fecha_proceso"].max().normalize() if stock["fecha_proceso"].notna().any() else (
            hist_full["fecha_desde"].max() if not hist_full.empty else None
        )
        if fecha_hist_min is not None:
            print(f"hist_ubicacion_sku: historia disponible desde {fecha_hist_min.date()} | cambios refrescados: {len(rows_hist):,}")
        else:
            print("hist_ubicacion_sku: sin historial disponible")

        # -------- Ubicación física real usando TODA la historia ya guardada --------
        venta_fisica = pd.DataFrame()
        cobertura_fisica = 0.0
        if not hist_full.empty and fecha_hist_min is not None and fecha_hist_max is not None:
            ventas_rec = ventas[(ventas["fecha_emision"].dt.normalize() >= fecha_hist_min) &
                                (ventas["fecha_emision"].dt.normalize() <= fecha_hist_max)].copy()
            if not ventas_rec.empty:
                ventas_rec["fecha_dia"] = ventas_rec["fecha_emision"].dt.normalize()
                ventas_rec["fecha_loc"] = ventas_rec["fecha_ubicacion_ref"].dt.normalize()
                left = ventas_rec.sort_values(["fecha_loc", "cod_tienda", "cod_rapido"])
                right = hist_full.rename(columns={"fecha_desde": "fecha_loc_hist"}).copy()
                right = right.sort_values(["fecha_loc_hist", "cod_tienda", "cod_rapido"])
                venta_fisica = pd.merge_asof(
                    left, right, left_on="fecha_loc", right_on="fecha_loc_hist",
                    by=["cod_tienda", "cod_rapido"], direction="backward", allow_exact_matches=True,
                )
                venta_fisica = venta_fisica[
                    venta_fisica["pasillo"].fillna("").astype(str).str.strip() != ""
                ].copy()
                den = float(ventas_rec["total"].abs().sum() or 0)
                cobertura_fisica = float(venta_fisica["total"].abs().sum()) / den if den else 0.0

        # -------- Dimensión: stock actual completo, incluso sin venta --------
        desc = (ventas_pos.sort_values("fecha_emision")
                .groupby(["cod_tienda", "cod_rapido"], as_index=False)
                .agg(descripcion=("descripcion", "last")))
        dim = stock.merge(desc, on=["cod_tienda", "cod_rapido"], how="left")

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
            "zona_pck=EXCLUDED.zona_pck, responsable_linea=COALESCE(EXCLUDED.responsable_linea,dim_producto_tienda.responsable_linea), "
            "pasillo=EXCLUDED.pasillo, rack=EXCLUDED.rack, actualizado=now()",
            rows_dim, page_size=5000)
        print(f"dim_producto_tienda: {len(rows_dim):,} filas")

        # Vista histórica por SURTIDO ACTUAL (para períodos sin ubicación física).
        attrs = stock[["cod_tienda", "cod_rapido", "pasillo", "rack"]].drop_duplicates(["cod_tienda", "cod_rapido"])
        venta_rack = ventas.merge(attrs, on=["cod_tienda", "cod_rapido"], how="left")
        sin_ubic = venta_rack["pasillo"].isna() | (venta_rack["pasillo"].fillna("").astype(str).str.strip() == "")
        venta_rack = venta_rack[~sin_ubic].copy()
        den_cov = float(ventas["total"].abs().sum() or 1)
        cobertura = float(venta_rack["total"].abs().sum()) / den_cov

        # -------- Hecho semanal: VENTA NETA sin IVA + NCV separada --------
        agg = (venta_rack.groupby(["cod_tienda", "anio", "mes", "semana", "pasillo", "rack", "cod_rapido"], as_index=False)
               .agg(venta=("total", "sum"), cantidad=("cantidad", "sum"),
                    venta_bruta=("venta_bruta", "sum"), ncv=("ncv", "sum"),
                    cantidad_bruta=("cantidad_bruta", "sum"), cantidad_ncv=("cantidad_ncv", "sum")))
        rows_fact = [(str(r.cod_tienda), int(r.anio), int(r.mes), int(r.semana), str(r.pasillo), str(r.rack),
                      str(r.cod_rapido), float(r.venta), 0.0, float(r.cantidad), float(r.venta_bruta),
                      float(r.ncv), float(r.cantidad_bruta), float(r.cantidad_ncv))
                     for r in agg.itertuples(index=False)]
        cur.execute("DELETE FROM fact_venta_semana WHERE cod_tienda = ANY(%s) AND anio = ANY(%s)", (tiendas, anios))
        if rows_fact:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO fact_venta_semana "
                "(cod_tienda,anio,mes,semana,pasillo,rack,cod_rapido,venta,margen,cantidad,venta_bruta,ncv,cantidad_bruta,cantidad_ncv) "
                "VALUES %s",
                rows_fact, page_size=5000)
        print(f"fact_venta_semana: {len(rows_fact):,} filas")

        # -------- Hecho físico diario: ubicación original real + fecha contable --------
        rows_fis = []
        if venta_fisica is not None and not venta_fisica.empty:
            vf = venta_fisica.copy()
            vf["anio"] = vf["fecha_dia"].dt.year.astype(int)
            vf["mes"] = vf["fecha_dia"].dt.month.astype(int)
            vf["semana"] = vf["fecha_dia"].dt.isocalendar().week.astype(int)
            agg_f = (vf.groupby(["cod_tienda","fecha_dia","anio","mes","semana","pasillo","rack","cod_rapido"], as_index=False)
                       .agg(venta=("total","sum"), cantidad=("cantidad","sum"),
                            venta_bruta=("venta_bruta","sum"), ncv=("ncv","sum"),
                            cantidad_bruta=("cantidad_bruta","sum"), cantidad_ncv=("cantidad_ncv","sum")))
            rows_fis = [(str(r.cod_tienda), r.fecha_dia.date(), int(r.anio), int(r.mes), int(r.semana),
                         str(r.pasillo), str(r.rack), str(r.cod_rapido), float(r.venta), float(r.cantidad),
                         float(r.venta_bruta), float(r.ncv), float(r.cantidad_bruta), float(r.cantidad_ncv))
                        for r in agg_f.itertuples(index=False)]

        if fecha_hist_min is not None:
            cur.execute("DELETE FROM fact_venta_rack_dia WHERE cod_tienda = ANY(%s) AND fecha >= %s",
                        (tiendas, fecha_hist_min.date()))
        if rows_fis:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO fact_venta_rack_dia "
                "(cod_tienda,fecha,anio,mes,semana,pasillo,rack,cod_rapido,venta,cantidad,venta_bruta,ncv,cantidad_bruta,cantidad_ncv) VALUES %s "
                "ON CONFLICT (cod_tienda,fecha,pasillo,rack,cod_rapido) DO UPDATE SET "
                "venta=EXCLUDED.venta,cantidad=EXCLUDED.cantidad,venta_bruta=EXCLUDED.venta_bruta,ncv=EXCLUDED.ncv,"
                "cantidad_bruta=EXCLUDED.cantidad_bruta,cantidad_ncv=EXCLUDED.cantidad_ncv",
                rows_fis, page_size=5000)
        if fecha_hist_min is not None:
            print(f"fact_venta_rack_dia: {len(rows_fis):,} filas | desde {fecha_hist_min.date()} | cobertura $ abs {cobertura_fisica:.1%}")
        else:
            print("fact_venta_rack_dia: sin historial de ubicación disponible")

        # -------- Cobertura física acumulada por tienda --------
        cur.execute("DELETE FROM sync_ubicacion_fisica WHERE cod_tienda = ANY(%s)", (tiendas,))
        meta_rows = []
        if not hist_full.empty:
            for cod_tienda, gh in hist_full.groupby("cod_tienda"):
                d0 = gh["fecha_desde"].min().date()
                stock_store = stock.loc[stock["cod_tienda"] == cod_tienda, "fecha_proceso"].dropna()
                d1 = stock_store.max().date() if len(stock_store) else (
                    pd.to_datetime(fecha_hist_max).date() if fecha_hist_max is not None else gh["fecha_desde"].max().date()
                )
                vr = ventas[(ventas["cod_tienda"] == cod_tienda) &
                            (ventas["fecha_emision"].dt.date >= d0) &
                            (ventas["fecha_emision"].dt.date <= d1)]
                vf_store = venta_fisica[venta_fisica["cod_tienda"] == cod_tienda] if not venta_fisica.empty else pd.DataFrame()
                den = float(vr["total"].abs().sum() or 0)
                cov = float(vf_store["total"].abs().sum()) / den if den else 0.0
                meta_rows.append((str(cod_tienda), d0, d1, round(cov, 4)))
        if meta_rows:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO sync_ubicacion_fisica (cod_tienda,fecha_desde,fecha_hasta,cobertura_venta_pct) VALUES %s "
                "ON CONFLICT (cod_tienda) DO UPDATE SET fecha_desde=EXCLUDED.fecha_desde, "
                "fecha_hasta=EXCLUDED.fecha_hasta,cobertura_venta_pct=EXCLUDED.cobertura_venta_pct,actualizado=now()",
                meta_rows, page_size=100)

        # -------- Agregados anuales: venta NETA sin IVA --------
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

        # -------- Comparativo: venta neta; trx/clientes solo documentos positivos --------
        comp_v = ventas.groupby(["cod_tienda", "anio"], as_index=False).agg(venta=("total", "sum"))
        comp_tc = ventas_pos.groupby(["cod_tienda", "anio"], as_index=False).agg(
            trx=("transaccion_id", "nunique"), clientes=("cod_entidad", "nunique"))
        comp = comp_v.merge(comp_tc, on=["cod_tienda", "anio"], how="left").fillna({"trx":0,"clientes":0})
        rows_comp = [(str(r.cod_tienda), int(r.anio), float(r.venta), int(r.trx), int(r.clientes))
                     for r in comp.itertuples(index=False)]
        if rows_comp:
            psycopg2.extras.execute_values(
                cur, "INSERT INTO fact_comparativo_anio (cod_tienda,anio,venta,trx,clientes) VALUES %s "
                     "ON CONFLICT (cod_tienda,anio) DO UPDATE SET venta=EXCLUDED.venta,trx=EXCLUDED.trx,clientes=EXCLUDED.clientes",
                rows_comp, page_size=1000)

        # -------- Cross-sell: SOLO FCV + BLV positivas, nunca NCV --------
        cs = calcular_cross_sell(ventas_pos[ventas_pos["anio"] == anio_actual].copy())
        cur.execute("DELETE FROM fact_cross_sell")
        if not cs.empty:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO fact_cross_sell (cod_tienda,sku_a,sku_b,desc_a,desc_b,boletas,soporte,confianza_a_b,confianza_b_a,lift) VALUES %s",
                list(cs.itertuples(index=False, name=None)), page_size=5000)

        ncv_abs = abs(float(ventas.loc[ventas["origen"] == "NCV", "total"].sum() or 0))
        bruto = float(ventas["venta_bruta"].sum() or 0)
        ncv_pct = ncv_abs / bruto if bruto else 0.0
        rango_fis = (f"{fecha_hist_min.date()}..{pd.to_datetime(fecha_hist_max).date()}" if fecha_hist_min is not None else "sin historial")
        mensaje = (f"OK V8 | años {','.join(map(str, anios))} | venta neta sin IVA | NCV {ncv_pct:.1%} bruto | "
                   f"ubicación surtido actual {cobertura:.1%} | ubicación física acumulada {rango_fis} cobertura abs {cobertura_fisica:.1%}")
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
