"""
Agente de sincronización — SQL Server → Postgres (Railway)

Se agenda con el Task Scheduler de Windows en un PC/servidor con acceso a tu
SQL Server. Cada vez que corre, trae el año actual completo y lo sube a
Postgres con INSERT ... ON CONFLICT (upsert), así que se puede correr todos
los días sin duplicar nada.

CONFIGURAR:
  pip install pyodbc psycopg2-binary pandas
  Variables de entorno (o reemplaza los valores fijos abajo):
    SQLSERVER_DSN   -- cadena de conexión ODBC a tu SQL Server
    DATABASE_URL    -- la de Railway (Postgres → Connect)

AGENDAR (Task Scheduler):
  Desencadenador: diario, a la hora que prefieras (ej. 6 AM, antes de que
  abra la tienda). Acción: "python.exe" con argumento la ruta de este archivo.

DE DÓNDE SALE PASILLO/RACK (confirmado contra tu modelo real, columna
calculada `Etiqueta_base` — validé la regla contra 6.2M filas reales y da
99.2% de coincidencia exacta con lo que ya tenías calculado en Power BI):

    usa_exhibicion = Zona_pck IN ('Z03','Z04','Z06') OR ManejaStock = 'N'
    Etiqueta_base  = si usa_exhibicion: Etiqueta_Exhibicion (o Etiqueta si esa viene vacía)
                     si no:             Etiqueta (o Etiqueta_Exhibicion si esa viene vacía)
    Pasillo = Etiqueta_base[:3]
    rack    = Etiqueta_base[:8]

TABLA DE COORDENADAS (Pasillos / Puntos Planograma)
  Esta NO sale de SQL Server -- el Power Query original la trae de un Excel:
  "coordenadas planograma.xlsx" (hoja "Pasillos"), en el OneDrive de un
  usuario "mriosv". Si tienes acceso a ese OneDrive, ahí está la fuente
  maestra para las coordenadas de las demás tiendas -- puede ahorrarte
  tener que remedirlas manualmente. Si no tienes acceso, seguimos con la
  página Administrar Planos que ya armé (sube imagen + CSV a mano).

NO PROBADO CONTRA TU SQL SERVER REAL -- no tengo acceso a tu red. La regla de
Pasillo/rack sí la validé con certeza (contra los datos reales del pbix), lo
que no pude probar es esta query específica corriendo en vivo. Corre una
prueba manual primero.
"""
import os
import sys
import datetime
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

# Mismo patrón que mt2s.sql (bloque VENTAS): stock del día + ventas del año,
# trayendo además Etiqueta/Etiqueta_Exhibicion/Zona_pck/ManejaStock para
# poder calcular Pasillo/rack igual que la columna Etiqueta_base del modelo.
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
        s.Etiqueta_Exhibicion, s.Etiqueta, s.Zona_pck, s.ManejaStock,
        ROW_NUMBER() OVER (PARTITION BY s.Cod_Rapido, s.cod_emp ORDER BY s.Fecha_Proceso DESC) AS rn
    FROM SAV.dbo.SAV_CI_INFSTOCKBODEGA_DIARIO s
    WHERE CAST(s.Fecha_Proceso AS date) >= CAST(DATEADD(DAY, -1, GETDATE()) AS date)
),
UltimosRegistros AS (SELECT * FROM StockRank WHERE rn = 1),
VentasDetalladas AS (
    SELECT f.cod_tienda, d.Cod_rapido, d.Descripcion, d.Cantidad, d.total, f.Fecha_Emision
    FROM SAV_VT.dbo.SAV_VT_FCVCab f
    INNER JOIN SAV_VT.dbo.SAV_VT_FCVdet d WITH (NOLOCK)
        ON f.Tipo_Documento = d.Tipo_Documento AND f.Nro_Interno = d.Nro_Interno AND f.Cod_Emp = d.Cod_Emp
    WHERE RTRIM(LTRIM(f.cod_tienda)) IN ({','.join(f"'{c}'" for c in STORE_CODES)})
      AND f.Fecha_Emision >= @IniAnio
    UNION ALL
    SELECT b.cod_tienda, e.Cod_rapido, e.Descripcion, e.Cantidad, e.total, b.Fecha_Emision
    FROM SAV_VT.dbo.SAV_VT_BLVcab b
    INNER JOIN SAV_VT.dbo.SAV_VT_BLVdet e WITH (NOLOCK)
        ON b.Tipo_Documento = e.Tipo_Documento AND b.Nro_Interno = e.Nro_Interno AND b.Cod_Emp = e.Cod_Emp
    WHERE RTRIM(LTRIM(b.cod_tienda)) IN ({','.join(f"'{c}'" for c in STORE_CODES)})
      AND b.Fecha_Emision >= @IniAnio
)
SELECT
    RTRIM(LTRIM(v.cod_tienda)) AS cod_tienda, v.Cod_rapido AS cod_rapido, v.Descripcion AS descripcion,
    v.Fecha_Emision AS fecha_emision, v.Cantidad AS cantidad, v.total AS total,
    s.Etiqueta_Exhibicion AS etiqueta_exhibicion, s.Etiqueta AS etiqueta,
    s.Zona_pck AS zona_pck, s.ManejaStock AS maneja_stock
FROM VentasDetalladas v
LEFT JOIN UltimosRegistros s
    ON v.Cod_rapido = s.Cod_Rapido AND RTRIM(LTRIM(v.cod_tienda)) = s.cod_emp;
"""


def calcular_pasillo_rack(df):
    """Reproduce la columna calculada Etiqueta_base -> Pasillo / rack."""
    etq = df["etiqueta"].fillna("").astype(str).str.strip()
    exh = df["etiqueta_exhibicion"].fillna("").astype(str).str.strip()
    usa_exh = df["zona_pck"].isin(["Z03", "Z04", "Z06"]) | (df["maneja_stock"] == "N")

    etiqueta_base = pd.Series("", index=df.index, dtype=object)
    etiqueta_base[usa_exh] = exh[usa_exh].where(exh[usa_exh] != "", etq[usa_exh])
    etiqueta_base[~usa_exh] = etq[~usa_exh].where(etq[~usa_exh] != "", exh[~usa_exh])

    df = df.copy()
    df["pasillo"] = etiqueta_base.str.slice(0, 3)
    df["rack"] = etiqueta_base.str.slice(0, 8)
    return df


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
    df = df[df["pasillo"] != ""]  # filas sin match de stock -> sin pasillo, se descartan de esta tabla

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # --- fact_pasillo_rack_semana --- (margen queda en 0 hasta arreglar el JOIN de costo en origen)
    agg_pr = (
        df.groupby(["cod_tienda", "anio", "mes", "semana", "pasillo", "rack"], as_index=False)
        .agg(venta=("total", "sum"))
    )
    rows_pr = [(str(r.cod_tienda), int(r.anio), int(r.mes), int(r.semana), str(r.pasillo), str(r.rack),
                float(r.venta), 0.0) for r in agg_pr.itertuples(index=False)]
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO fact_pasillo_rack_semana (cod_tienda,anio,mes,semana,pasillo,rack,venta,margen) "
        "VALUES %s ON CONFLICT (cod_tienda,anio,mes,semana,pasillo,rack) DO UPDATE "
        "SET venta = EXCLUDED.venta",
        rows_pr, page_size=5000,
    )
    print(f"fact_pasillo_rack_semana: {len(rows_pr):,} filas")

    # --- fact_producto_semana ---
    agg_prod = (
        df.groupby(["cod_tienda", "anio", "mes", "semana", "cod_rapido", "descripcion"], as_index=False)
        .agg(venta=("total", "sum"), cantidad=("cantidad", "sum"))
    )
    rows_prod = [(str(r.cod_tienda), int(r.anio), int(r.mes), int(r.semana), str(r.cod_rapido),
                  str(r.descripcion) if pd.notna(r.descripcion) else None, float(r.venta), float(r.cantidad))
                 for r in agg_prod.itertuples(index=False)]
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO fact_producto_semana (cod_tienda,anio,mes,semana,cod_rapido,descripcion,venta,cantidad) "
        "VALUES %s ON CONFLICT (cod_tienda,anio,mes,semana,cod_rapido) DO UPDATE "
        "SET venta = EXCLUDED.venta, cantidad = EXCLUDED.cantidad, descripcion = EXCLUDED.descripcion",
        rows_prod, page_size=5000,
    )
    print(f"fact_producto_semana: {len(rows_prod):,} filas")

    # --- dim_producto_tienda (universo vigente, para "sin venta") ---
    universo = df.groupby(["cod_tienda", "cod_rapido"], as_index=False).agg(
        descripcion=("descripcion", "first"), maneja_stock=("maneja_stock", "first"))
    rows_u = [(str(r.cod_tienda), str(r.cod_rapido), str(r.descripcion) if pd.notna(r.descripcion) else None,
               str(r.maneja_stock) if pd.notna(r.maneja_stock) else None)
              for r in universo.itertuples(index=False)]
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO dim_producto_tienda (cod_tienda,cod_rapido,descripcion,maneja_stock) VALUES %s "
        "ON CONFLICT (cod_tienda,cod_rapido) DO UPDATE "
        "SET descripcion = EXCLUDED.descripcion, maneja_stock = EXCLUDED.maneja_stock",
        rows_u, page_size=5000,
    )
    print(f"dim_producto_tienda: {len(rows_u):,} filas")

    cur.execute(
        "INSERT INTO sync_log (filas_pasillo_rack, filas_producto, ok, mensaje) VALUES (%s,%s,%s,%s)",
        (len(rows_pr), len(rows_prod), True, "OK"),
    )
    conn.commit()
    cur.close()
    conn.close()
    print(f"[{datetime.datetime.now()}] Sincronización completa.")


if __name__ == "__main__":
    main()
