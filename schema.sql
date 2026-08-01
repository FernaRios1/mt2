-- Esquema Postgres — Rentabilidad Rack (Imperial Ferretería) — v2
-- Una sola tabla de hechos a nivel semana/pasillo/rack/producto, con todas
-- las dimensiones de clasificación como columnas -- así los filtros
-- (categoría, familia, clasificación, maneja stock, zona de picking, jefe
-- de línea) aplican igual sobre el mapa de calor, las tablas de pasillo/rack
-- y las de producto, todo desde el mismo lugar.

CREATE TABLE IF NOT EXISTS dim_tienda (
    cod_tienda   VARCHAR(10) PRIMARY KEY,
    nombre       VARCHAR(100),
    tipo         VARCHAR(30)                 -- AUTOSERVICIO / TRADICIONAL
);

CREATE TABLE IF NOT EXISTS fact_venta_semana (
    id                 BIGSERIAL PRIMARY KEY,
    cod_tienda         VARCHAR(10) NOT NULL REFERENCES dim_tienda(cod_tienda),
    anio               SMALLINT NOT NULL,
    mes                SMALLINT NOT NULL,
    semana             SMALLINT NOT NULL,
    pasillo            VARCHAR(20) NOT NULL,
    rack               VARCHAR(20) NOT NULL DEFAULT '',
    cod_rapido         VARCHAR(30) NOT NULL,
    descripcion        VARCHAR(200),
    familia            VARCHAR(100),
    subfamilia         VARCHAR(100),
    categoria          VARCHAR(100),
    clasificacion      VARCHAR(50),
    maneja_stock       CHAR(1),
    zona_pck           VARCHAR(10),
    responsable_linea  VARCHAR(50),
    venta              NUMERIC(14,2) NOT NULL DEFAULT 0,
    margen             NUMERIC(14,2) NOT NULL DEFAULT 0,
    cantidad           NUMERIC(14,2) NOT NULL DEFAULT 0,
    UNIQUE (cod_tienda, anio, mes, semana, pasillo, rack, cod_rapido)
);
CREATE INDEX IF NOT EXISTS idx_fvs_tienda_periodo ON fact_venta_semana (cod_tienda, anio, mes);
CREATE INDEX IF NOT EXISTS idx_fvs_pasillo ON fact_venta_semana (cod_tienda, pasillo);
CREATE INDEX IF NOT EXISTS idx_fvs_familia ON fact_venta_semana (familia);
CREATE INDEX IF NOT EXISTS idx_fvs_categoria ON fact_venta_semana (categoria);
CREATE INDEX IF NOT EXISTS idx_fvs_clasificacion ON fact_venta_semana (clasificacion);

-- Universo de productos vigentes por tienda (para "sin venta" -- productos
-- con stock que no aparecen en fact_venta_semana en el período elegido).
CREATE TABLE IF NOT EXISTS dim_producto_tienda (
    cod_tienda    VARCHAR(10) NOT NULL REFERENCES dim_tienda(cod_tienda),
    cod_rapido    VARCHAR(30) NOT NULL,
    descripcion   VARCHAR(200),
    maneja_stock  CHAR(1),
    familia       VARCHAR(100),
    categoria     VARCHAR(100),
    actualizado   TIMESTAMP DEFAULT now(),
    PRIMARY KEY (cod_tienda, cod_rapido)
);

-- Coordenadas de cada PASILLO sobre el plano (mapa de calor, nivel pasillo).
CREATE TABLE IF NOT EXISTS dim_pasillo_coord (
    cod_tienda  VARCHAR(10) NOT NULL REFERENCES dim_tienda(cod_tienda),
    pasillo     VARCHAR(20) NOT NULL,
    x           INTEGER NOT NULL,
    y           INTEGER NOT NULL,
    PRIMARY KEY (cod_tienda, pasillo)
);

-- Coordenadas de cada RACK sobre el plano (mapa de calor, nivel rack -- más fino).
CREATE TABLE IF NOT EXISTS dim_rack_coord (
    cod_tienda  VARCHAR(10) NOT NULL REFERENCES dim_tienda(cod_tienda),
    rack        VARCHAR(20) NOT NULL,
    x           INTEGER NOT NULL,
    y           INTEGER NOT NULL,
    PRIMARY KEY (cod_tienda, rack)
);

-- Imagen del plano de cada tienda (se sube desde la pestaña Administrar Planos).
CREATE TABLE IF NOT EXISTS dim_plano (
    cod_tienda   VARCHAR(10) PRIMARY KEY REFERENCES dim_tienda(cod_tienda),
    imagen       BYTEA NOT NULL,
    img_w        INTEGER NOT NULL,
    img_h        INTEGER NOT NULL,
    actualizado  TIMESTAMP DEFAULT now()
);

-- Historial de corridas del agente de sincronización.
CREATE TABLE IF NOT EXISTS sync_log (
    id                  SERIAL PRIMARY KEY,
    ejecutado_en        TIMESTAMP DEFAULT now(),
    filas_pasillo_rack  INTEGER,
    filas_producto      INTEGER,
    ok                  BOOLEAN,
    mensaje             TEXT
);
