"""
Carga inicial de Postgres con los datos reales ya extraídos del Mt2s.pbix
(año 2026, grano semanal, con familia/categoría/clasificación/zona de
picking/jefe de línea para los filtros).

YA NO ES NECESARIO CORRERLO A MANO: la app (db.ensure_ready(), llamado al
abrir app.py o la página Administrar Planos) hace esto mismo sola la primera
vez que alguien entra con la base vacía. Este script queda por si prefieres
cargar los datos ANTES de abrir la app, o para restaurarlos en otro ambiente.

Uso:
    pip install psycopg2-binary pandas Pillow
    DATABASE_URL="postgresql://usuario:pass@host:puerto/db" python cargar_datos_iniciales.py
"""
import os
import sys
import io
import pandas as pd
import psycopg2

DSN = os.environ.get("DATABASE_URL")
if not DSN:
    print("Falta la variable DATABASE_URL (la de Railway, pestaña Postgres → Connect).")
    sys.exit(1)
DSN = DSN.replace("postgres://", "postgresql://", 1)

HERE = os.path.dirname(os.path.abspath(__file__))


def _cargar(cur, tabla, cols, archivo):
    path = os.path.join(HERE, archivo)
    if not os.path.exists(path):
        print(f"  (omitido, no existe {archivo})")
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
    return len(df)


def main():
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()

    cur.execute("SELECT to_regclass('public.dim_tienda')")
    if cur.fetchone()[0] is not None:
        print("La base ya tiene tablas -- no hago nada (evito duplicar). "
              "Si quieres recargar de cero, borra las tablas primero.")
        return

    with open(os.path.join(HERE, "schema.sql"), encoding="utf-8") as f:
        cur.execute(f.read())

    n1 = _cargar(cur, "dim_tienda", ["cod_tienda", "nombre", "tipo"], "seed_tiendas.csv")
    print(f"dim_tienda: {n1} filas")

    n2 = _cargar(cur, "fact_venta_semana",
                 ["cod_tienda", "anio", "mes", "semana", "pasillo", "rack", "cod_rapido", "descripcion",
                  "familia", "subfamilia", "categoria", "clasificacion", "maneja_stock", "zona_pck",
                  "responsable_linea", "venta", "cantidad"],
                 "seed_fact_venta_semana.csv.gz")
    print(f"fact_venta_semana: {n2} filas")

    n3 = _cargar(cur, "dim_producto_tienda", ["cod_tienda", "cod_rapido", "descripcion", "maneja_stock"],
                 "seed_producto_tienda.csv.gz")
    print(f"dim_producto_tienda: {n3} filas")

    plano_path = os.path.join(HERE, "seed_plano_sanro.png")
    if os.path.exists(plano_path):
        from PIL import Image
        img = Image.open(plano_path)
        with open(plano_path, "rb") as f:
            cur.execute(
                "INSERT INTO dim_plano (cod_tienda, imagen, img_w, img_h) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (cod_tienda) DO UPDATE SET imagen=EXCLUDED.imagen",
                ("SANRO", psycopg2.Binary(f.read()), img.width, img.height),
            )
        n4 = _cargar(cur, "dim_pasillo_coord", ["cod_tienda", "pasillo", "x", "y"], "seed_coords_sanro.csv")
        n5 = _cargar(cur, "dim_rack_coord", ["cod_tienda", "rack", "x", "y"], "seed_rack_coords_sanro.csv")
        print(f"plano SANRO + {n4} coords de pasillo + {n5} coords de rack")

    conn.commit()
    cur.close()
    conn.close()
    print("Listo.")


if __name__ == "__main__":
    main()
