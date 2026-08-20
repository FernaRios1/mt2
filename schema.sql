-- Esquema Postgres — Rentabilidad Rack (Imperial Ferretería) — versión Dash

CREATE TABLE IF NOT EXISTS dim_tienda (
    cod_tienda   VARCHAR(10) PRIMARY KEY,
    nombre       VARCHAR(100),
    tipo         VARCHAR(30)
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
    venta              NUMERIC(14,2) NOT NULL DEFAULT 0,
    margen             NUMERIC(14,2) NOT NULL DEFAULT 0,
    cantidad           NUMERIC(14,2) NOT NULL DEFAULT 0,
    UNIQUE (cod_tienda, anio, mes, semana, pasillo, rack, cod_rapido)
);
CREATE INDEX IF NOT EXISTS idx_fvs_tienda_periodo ON fact_venta_semana (cod_tienda, anio, mes);
CREATE INDEX IF NOT EXISTS idx_fvs_pasillo ON fact_venta_semana (cod_tienda, pasillo);
CREATE INDEX IF NOT EXISTS idx_fvs_cod_rapido ON fact_venta_semana (cod_tienda, cod_rapido);

-- Dimensión de producto: atributos + stock, para joinear con fact_venta_semana
-- y así poder filtrar por familia/categoría/clasificación/marca/etc.
CREATE TABLE IF NOT EXISTS dim_producto_tienda (
    cod_tienda        VARCHAR(10) NOT NULL REFERENCES dim_tienda(cod_tienda),
    cod_rapido        VARCHAR(30) NOT NULL,
    descripcion       VARCHAR(200),
    marca             VARCHAR(100),
    stock             NUMERIC(12,2),
    familia           VARCHAR(100),
    subfamilia        VARCHAR(100),
    categoria         VARCHAR(100),
    clasificacion     VARCHAR(50),
    maneja_stock      CHAR(1),
    zona_pck          VARCHAR(10),
    responsable_linea VARCHAR(50),
    actualizado       TIMESTAMP DEFAULT now(),
    PRIMARY KEY (cod_tienda, cod_rapido)
);
CREATE INDEX IF NOT EXISTS idx_dpt_familia ON dim_producto_tienda (familia);
CREATE INDEX IF NOT EXISTS idx_dpt_categoria ON dim_producto_tienda (categoria);

CREATE TABLE IF NOT EXISTS fact_pasillo_rack_anio (
    cod_tienda  VARCHAR(10) NOT NULL REFERENCES dim_tienda(cod_tienda),
    anio        SMALLINT NOT NULL,
    pasillo     VARCHAR(20) NOT NULL,
    rack        VARCHAR(20) NOT NULL DEFAULT '',
    venta       NUMERIC(14,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (cod_tienda, anio, pasillo, rack)
);

CREATE TABLE IF NOT EXISTS fact_producto_anio (
    cod_tienda  VARCHAR(10) NOT NULL REFERENCES dim_tienda(cod_tienda),
    anio        SMALLINT NOT NULL,
    cod_rapido  VARCHAR(30) NOT NULL,
    venta       NUMERIC(14,2) NOT NULL DEFAULT 0,
    cantidad    NUMERIC(14,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (cod_tienda, anio, cod_rapido)
);

-- Comparativo tienda completa año actual vs año anterior (venta, trx, clientes).
CREATE TABLE IF NOT EXISTS fact_comparativo_anio (
    cod_tienda  VARCHAR(10) NOT NULL REFERENCES dim_tienda(cod_tienda),
    anio        SMALLINT NOT NULL,
    venta       NUMERIC(16,2) NOT NULL DEFAULT 0,
    trx         INTEGER NOT NULL DEFAULT 0,
    clientes    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (cod_tienda, anio)
);

-- Cross-sell: pares de producto con soporte/confianza/lift.
CREATE TABLE IF NOT EXISTS fact_cross_sell (
    id              BIGSERIAL PRIMARY KEY,
    cod_tienda      VARCHAR(10) NOT NULL REFERENCES dim_tienda(cod_tienda),
    sku_a           VARCHAR(30) NOT NULL,
    sku_b           VARCHAR(30) NOT NULL,
    desc_a          VARCHAR(200),
    desc_b          VARCHAR(200),
    boletas         INTEGER NOT NULL,
    soporte         NUMERIC(8,5),
    confianza_a_b   NUMERIC(6,4),
    confianza_b_a   NUMERIC(6,4),
    lift            NUMERIC(10,3)
);
CREATE INDEX IF NOT EXISTS idx_cs_tienda ON fact_cross_sell (cod_tienda, boletas DESC);
CREATE INDEX IF NOT EXISTS idx_cs_sku_a ON fact_cross_sell (cod_tienda, sku_a);
CREATE INDEX IF NOT EXISTS idx_cs_sku_b ON fact_cross_sell (cod_tienda, sku_b);

CREATE TABLE IF NOT EXISTS dim_pasillo_coord (
    cod_tienda  VARCHAR(10) NOT NULL REFERENCES dim_tienda(cod_tienda),
    pasillo     VARCHAR(20) NOT NULL,
    x           INTEGER NOT NULL,
    y           INTEGER NOT NULL,
    PRIMARY KEY (cod_tienda, pasillo)
);

CREATE TABLE IF NOT EXISTS dim_rack_coord (
    cod_tienda  VARCHAR(10) NOT NULL REFERENCES dim_tienda(cod_tienda),
    rack        VARCHAR(20) NOT NULL,
    x           INTEGER NOT NULL,
    y           INTEGER NOT NULL,
    PRIMARY KEY (cod_tienda, rack)
);

CREATE TABLE IF NOT EXISTS dim_plano (
    cod_tienda   VARCHAR(10) PRIMARY KEY REFERENCES dim_tienda(cod_tienda),
    imagen       BYTEA NOT NULL,
    img_w        INTEGER NOT NULL,
    img_h        INTEGER NOT NULL,
    actualizado  TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sync_log (
    id                  SERIAL PRIMARY KEY,
    ejecutado_en        TIMESTAMP DEFAULT now(),
    filas_venta         INTEGER,
    ok                  BOOLEAN,
    mensaje             TEXT
);

-- V3: ubicación vigente del producto en la tienda. Permite analizar también
-- productos con stock pero sin venta, que no aparecen en fact_venta_semana.
ALTER TABLE dim_producto_tienda ADD COLUMN IF NOT EXISTS pasillo VARCHAR(20);
ALTER TABLE dim_producto_tienda ADD COLUMN IF NOT EXISTS rack VARCHAR(20);
CREATE INDEX IF NOT EXISTS idx_dpt_pasillo ON dim_producto_tienda (cod_tienda, pasillo);
CREATE INDEX IF NOT EXISTS idx_dpt_rack ON dim_producto_tienda (cod_tienda, rack);
