"""
Agente de sincronización — SQL Server → Postgres (Railway)

Se agenda con el Task Scheduler de Windows en un PC/servidor con acceso a tu
SQL Server. Cada vez que corre, trae la semana más reciente y la sube a
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

LO QUE FALTA — IMPORTANTE
  La query de abajo trae venta por producto correctamente (mismo patrón que
  tu VENTAS de mt2s.sql, con el join de costo arreglado). Pero **no incluye
  Pasillo, rack ni ManejaStock** porque esas columnas no estaban en el SQL
  que me pasaste — deben salir de otra tabla o de un merge que armas en
  Power Query, y no tengo visibilidad de eso. Hasta que me digas de dónde
  sale esa asignación, este agente actualiza fact_producto_semana
  correctamente, pero NO toca fact_pasillo_rack_semana (para no pisar los
  datos reales que sí cargué desde tu pbix con cargar_datos_iniciales.py).
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

QUERY_PRODUCTOS = f"""
DECLARE @IniAnio date = DATEFROMPARTS(YEAR(GETDATE()), 1, 1);

WITH VentasDetalladas AS (
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
SELECT RTRIM(LTRIM(cod_tienda)) AS cod_tienda, Cod_rapido AS cod_rapido, Descripcion AS descripcion,
       Fecha_Emision AS fecha_emision, Cantidad AS cantidad, total AS total
FROM VentasDetalladas;
"""


def semana_del_anio(fecha):
    """Semana corrida del año, mismo criterio que la columna 'Semana' de tu modelo actual."""
    return fecha.isocalendar()[1]


def main():
    if not DATABASE_URL:
        print("Falta DATABASE_URL"); sys.exit(1)

    print(f"[{datetime.datetime.now()}] Conectando a SQL Server...")
    with pyodbc.connect(SQLSERVER_DSN) as cn:
        df = pd.read_sql(QUERY_PRODUCTOS, cn)
    print(f"{len(df):,} filas traídas de SQL Server.")

    df["fecha_emision"] = pd.to_datetime(df["fecha_emision"])
    df["anio"] = df["fecha_emision"].dt.year
    df["mes"] = df["fecha_emision"].dt.month
    df["semana"] = df["fecha_emision"].dt.isocalendar().week

    agg = (
        df.groupby(["cod_tienda", "anio", "mes", "semana", "cod_rapido", "descripcion"], as_index=False)
        .agg(venta=("total", "sum"), cantidad=("cantidad", "sum"))
    )

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    rows = [(str(r.cod_tienda), int(r.anio), int(r.mes), int(r.semana), str(r.cod_rapido),
              str(r.descripcion) if pd.notna(r.descripcion) else None,
              float(r.venta), float(r.cantidad))
             for r in agg.itertuples(index=False)]
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO fact_producto_semana (cod_tienda,anio,mes,semana,cod_rapido,descripcion,venta,cantidad) "
        "VALUES %s "
        "ON CONFLICT (cod_tienda,anio,mes,semana,cod_rapido) DO UPDATE "
        "SET venta = EXCLUDED.venta, cantidad = EXCLUDED.cantidad, descripcion = EXCLUDED.descripcion",
        rows, page_size=5000,
    )

    # refresca también el universo de productos vigentes (para "sin venta")
    universo = df.groupby(["cod_tienda", "cod_rapido"], as_index=False).agg(descripcion=("descripcion", "first"))
    rows_u = [(str(r.cod_tienda), str(r.cod_rapido), str(r.descripcion) if pd.notna(r.descripcion) else None, "S")
              for r in universo.itertuples(index=False)]
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO dim_producto_tienda (cod_tienda,cod_rapido,descripcion,maneja_stock) VALUES %s "
        "ON CONFLICT (cod_tienda,cod_rapido) DO UPDATE SET descripcion = EXCLUDED.descripcion",
        rows_u, page_size=5000,
    )

    cur.execute(
        "INSERT INTO sync_log (filas_pasillo_rack, filas_producto, ok, mensaje) VALUES (%s,%s,%s,%s)",
        (0, len(rows), True, "Sync producto OK. Pasillo/rack NO actualizado -- falta origen de esa columna."),
    )
    conn.commit()
    cur.close()
    conn.close()
    print(f"[{datetime.datetime.now()}] {len(rows)} filas de producto actualizadas en Postgres.")
    print("Pasillo/rack no se tocó -- ver nota al inicio del archivo.")


if __name__ == "__main__":
    main()
