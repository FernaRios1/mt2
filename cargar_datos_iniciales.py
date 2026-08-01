"""
Carga inicial de Postgres con los datos reales que ya extraje del Mt2s.pbix
que me enviaste (año 2026, grano semanal). Corre esto UNA vez después de crear
la base en Railway, para no empezar de cero — después el agente de
sincronización la va manteniendo al día.

Uso:
    pip install psycopg2-binary pandas
    DATABASE_URL="postgresql://usuario:pass@host:puerto/db" python cargar_datos_iniciales.py
"""
import os
import sys
import pandas as pd
import psycopg2
import psycopg2.extras

DSN = os.environ.get("DATABASE_URL")
if not DSN:
    print("Falta la variable DATABASE_URL (la de Railway, pestaña Postgres → Connect).")
    sys.exit(1)
DSN = DSN.replace("postgres://", "postgresql://", 1)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    tiendas = pd.read_csv(os.path.join(here, "seed_tiendas.csv"))
    pasillo_rack = pd.read_csv(os.path.join(here, "seed_pasillo_rack_semana.csv.gz"))
    productos = pd.read_csv(os.path.join(here, "seed_producto_semana.csv.gz"))
    universo = pd.read_csv(os.path.join(here, "seed_producto_tienda.csv.gz"))

    conn = psycopg2.connect(DSN)
    cur = conn.cursor()

    with open(os.path.join(here, "schema.sql"), encoding="utf-8") as f:
        cur.execute(f.read())

    psycopg2.extras.execute_values(
        cur, "INSERT INTO dim_tienda (cod_tienda,nombre,tipo) VALUES %s ON CONFLICT (cod_tienda) DO NOTHING",
        list(tiendas.itertuples(index=False, name=None)), page_size=1000)
    print(f"dim_tienda: {len(tiendas)} filas")

    psycopg2.extras.execute_values(
        cur, "INSERT INTO fact_pasillo_rack_semana (cod_tienda,anio,mes,semana,pasillo,rack,venta,margen) "
             "VALUES %s ON CONFLICT DO NOTHING",
        list(pasillo_rack.itertuples(index=False, name=None)), page_size=5000)
    print(f"fact_pasillo_rack_semana: {len(pasillo_rack)} filas")

    psycopg2.extras.execute_values(
        cur, "INSERT INTO fact_producto_semana (cod_tienda,anio,mes,semana,cod_rapido,descripcion,venta,cantidad) "
             "VALUES %s ON CONFLICT DO NOTHING",
        list(productos.itertuples(index=False, name=None)), page_size=5000)
    print(f"fact_producto_semana: {len(productos)} filas")

    psycopg2.extras.execute_values(
        cur, "INSERT INTO dim_producto_tienda (cod_tienda,cod_rapido,descripcion,maneja_stock) "
             "VALUES %s ON CONFLICT DO NOTHING",
        list(universo.itertuples(index=False, name=None)), page_size=5000)
    print(f"dim_producto_tienda: {len(universo)} filas")

    # Plano y coordenadas de SANRO (el único que ya tengo digitalizado)
    coords_sanro = pd.read_csv(os.path.join(here, "seed_coords_sanro.csv"))
    plano_path = os.path.join(here, "seed_plano_sanro.png")
    if os.path.exists(plano_path):
        from PIL import Image
        img = Image.open(plano_path)
        with open(plano_path, "rb") as f:
            cur.execute(
                "INSERT INTO dim_plano (cod_tienda, imagen, img_w, img_h) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (cod_tienda) DO UPDATE SET imagen=EXCLUDED.imagen",
                ("SANRO", psycopg2.Binary(f.read()), img.width, img.height),
            )
        psycopg2.extras.execute_values(
            cur, "INSERT INTO dim_pasillo_coord (cod_tienda,pasillo,x,y) VALUES %s ON CONFLICT DO NOTHING",
            list(coords_sanro.itertuples(index=False, name=None)), page_size=1000)
        print(f"plano + {len(coords_sanro)} coordenadas de SANRO cargadas")

    conn.commit()
    cur.close()
    conn.close()
    print("Listo. Esto es el snapshot de 2026 que ya tenías en el pbix — el agente se")
    print("encarga de ir sumando semanas nuevas desde ahora en adelante.")


if __name__ == "__main__":
    main()
