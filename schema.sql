-- Esquema Postgres — Rentabilidad Rack (Imperial Ferretería)
-- Diseñado para permitir filtrar por mes y por semana sin reprocesar nada,
-- y para poder ir sumando el plano de cada tienda con el tiempo.

CREATE TABLE IF NOT EXISTS dim_tienda (
    cod_tienda   VARCHAR(10) PRIMARY KEY,
    nombre       VARCHAR(100),
    tipo         VARCHAR(30)                 -- AUTOSERVICIO / TRADICIONAL
);

-- Venta y margen por pasillo/rack, a nivel semana (permite reconstruir mes sumando semanas).
CREATE TABLE IF NOT EXISTS fact_pasillo_rack_semana (
    cod_tienda     VARCHAR(10) NOT NULL REFERENCES dim_tienda(cod_tienda),
    anio           SMALLINT NOT NULL,
    mes            SMALLINT NOT NULL,
    semana         SMALLINT NOT NULL,        -- semana corrida del año (1-53)
    pasillo        VARCHAR(20) NOT NULL,
    rack           VARCHAR(20) NOT NULL DEFAULT '',
    venta          NUMERIC(14,2) NOT NULL DEFAULT 0,
    margen         NUMERIC(14,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (cod_tienda, anio, mes, semana, pasillo, rack)
);
CREATE INDEX IF NOT EXISTS idx_prs_tienda_periodo ON fact_pasillo_rack_semana (cod_tienda, anio, mes);

-- Venta por producto, misma granularidad semana/mes.
CREATE TABLE IF NOT EXISTS fact_producto_semana (
    cod_tienda     VARCHAR(10) NOT NULL REFERENCES dim_tienda(cod_tienda),
    anio           SMALLINT NOT NULL,
    mes            SMALLINT NOT NULL,
    semana         SMALLINT NOT NULL,
    cod_rapido     VARCHAR(30) NOT NULL,
    descripcion    VARCHAR(200),
    venta          NUMERIC(14,2) NOT NULL DEFAULT 0,
    cantidad       NUMERIC(14,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (cod_tienda, anio, mes, semana, cod_rapido)
);
CREATE INDEX IF NOT EXISTS idx_fps_tienda_periodo ON fact_producto_semana (cod_tienda, anio, mes);

-- Universo de productos vigentes por tienda (para calcular "sin venta" sin
-- tener que guardar una fila en cero por cada semana x producto).
CREATE TABLE IF NOT EXISTS dim_producto_tienda (
    cod_tienda    VARCHAR(10) NOT NULL REFERENCES dim_tienda(cod_tienda),
    cod_rapido    VARCHAR(30) NOT NULL,
    descripcion   VARCHAR(200),
    maneja_stock  CHAR(1),
    actualizado   TIMESTAMP DEFAULT now(),
    PRIMARY KEY (cod_tienda, cod_rapido)
);

-- Coordenadas X/Y de cada pasillo sobre el plano (para el mapa de calor).
CREATE TABLE IF NOT EXISTS dim_pasillo_coord (
    cod_tienda  VARCHAR(10) NOT NULL REFERENCES dim_tienda(cod_tienda),
    pasillo     VARCHAR(20) NOT NULL,
    x           INTEGER NOT NULL,
    y           INTEGER NOT NULL,
    PRIMARY KEY (cod_tienda, pasillo)
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
