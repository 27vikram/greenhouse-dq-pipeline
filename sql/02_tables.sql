/* =====================================================================
   02_tables.sql — every table in GreenhouseDW
   Flow:  files -> stg (all text) -> DQ rules -> dw (typed) | err.quarantine
   Re-runnable: drops everything first. DEV ONLY.
   Note: the fact tables have no nonclustered indexes yet. They are added
   on Day 2 as the before/after performance tuning exercise.
   ===================================================================== */
SET NOCOUNT ON;

-- drop in dependency order (children before parents)
DROP TABLE IF EXISTS dw.fact_shipment_line, dw.fact_harvest, dw.fact_climate_hourly;
DROP TABLE IF EXISTS dw.dim_sku, dw.dim_customer, dw.dim_zone, dw.dim_crop, dw.dim_date;
DROP TABLE IF EXISTS err.quarantine;
DROP TABLE IF EXISTS dq.result, dq.check_rule;
DROP TABLE IF EXISTS stg.climate_reading, stg.harvest, stg.shipment_line;
DROP TABLE IF EXISTS ops.file_load, ops.pipeline_run;
DROP TABLE IF EXISTS ref.crop_alias, ref.sku, ref.zone, ref.crop, ref.sensor_valid_range;
GO

/* ------------------------------------------------------------- ref */
CREATE TABLE ref.crop (
    crop_name    NVARCHAR(50) NOT NULL CONSTRAINT PK_ref_crop PRIMARY KEY,
    crop_family  NVARCHAR(30) NOT NULL
);

CREATE TABLE ref.zone (
    zone_id     VARCHAR(10)  NOT NULL CONSTRAINT PK_ref_zone PRIMARY KEY,
    greenhouse  VARCHAR(10)  NOT NULL,
    crop_name   NVARCHAR(50) NOT NULL CONSTRAINT FK_ref_zone_crop REFERENCES ref.crop(crop_name),
    area_m2     INT          NOT NULL CONSTRAINT CK_ref_zone_area CHECK (area_m2 > 0)
);

CREATE TABLE ref.sku (
    sku          VARCHAR(20)   NOT NULL CONSTRAINT PK_ref_sku PRIMARY KEY,
    description  NVARCHAR(100) NOT NULL,
    crop_name    NVARCHAR(50)  NOT NULL CONSTRAINT FK_ref_sku_crop REFERENCES ref.crop(crop_name),
    case_kg      DECIMAL(6,2)  NOT NULL CONSTRAINT CK_ref_sku_case_kg CHECK (case_kg > 0)
);

-- Maps spelling variants seen in source systems to the master crop name.
-- Starts EMPTY on purpose: filling it is the fix that comes out of the RCA.
CREATE TABLE ref.crop_alias (
    alias      NVARCHAR(50)  NOT NULL CONSTRAINT PK_ref_crop_alias PRIMARY KEY,
    crop_name  NVARCHAR(50)  NOT NULL CONSTRAINT FK_ref_crop_alias_crop REFERENCES ref.crop(crop_name),
    reason     NVARCHAR(400) NULL,
    added_by   NVARCHAR(128) NOT NULL CONSTRAINT DF_crop_alias_by DEFAULT SUSER_SNAME(),
    added_at   DATETIME2(0)  NOT NULL CONSTRAINT DF_crop_alias_at DEFAULT SYSUTCDATETIME()
);

-- Thresholds live in a table, not in code, so the business can change them without a deploy.
CREATE TABLE ref.sensor_valid_range (
    metric     VARCHAR(30)   NOT NULL CONSTRAINT PK_ref_sensor_valid_range PRIMARY KEY,
    unit       NVARCHAR(20)  NOT NULL,
    min_valid  DECIMAL(9,2)  NOT NULL,
    max_valid  DECIMAL(9,2)  NOT NULL,
    note       NVARCHAR(200) NULL,
    CONSTRAINT CK_sensor_range CHECK (min_valid < max_valid)
);

/* ------------------------------------------------------------- ops */
CREATE TABLE ops.pipeline_run (
    run_id        INT IDENTITY(1,1) CONSTRAINT PK_ops_pipeline_run PRIMARY KEY,
    started_at    DATETIME2(0)   NOT NULL CONSTRAINT DF_run_started DEFAULT SYSUTCDATETIME(),
    ended_at      DATETIME2(0)   NULL,
    status        VARCHAR(30)    NOT NULL CONSTRAINT DF_run_status DEFAULT 'running',
    triggered_by  NVARCHAR(128)  NOT NULL CONSTRAINT DF_run_by DEFAULT SUSER_SNAME(),
    message       NVARCHAR(4000) NULL,
    CONSTRAINT CK_run_status CHECK (status IN ('running','succeeded','succeeded_with_warnings','failed'))
);

CREATE TABLE ops.file_load (
    load_id        INT IDENTITY(1,1) CONSTRAINT PK_ops_file_load PRIMARY KEY,
    run_id         INT            NOT NULL CONSTRAINT FK_file_load_run REFERENCES ops.pipeline_run(run_id),
    source_system  VARCHAR(30)    NOT NULL,
    file_name      NVARCHAR(260)  NOT NULL,
    file_hash      CHAR(64)       NOT NULL,          -- SHA-256 of the file content
    rows_in_file   INT            NULL,
    rows_loaded    INT            NULL,
    status         VARCHAR(20)    NOT NULL CONSTRAINT DF_file_status DEFAULT 'loading',
    message        NVARCHAR(4000) NULL,
    loaded_at      DATETIME2(0)   NOT NULL CONSTRAINT DF_file_loaded DEFAULT SYSUTCDATETIME(),
    processed_run_id INT          NULL,             -- run that pushed this file through DQ into dw (NULL = pending)
    CONSTRAINT CK_file_status CHECK (status IN ('loading','loaded','row_mismatch','rejected','failed'))
);
-- the same file content can only be loaded successfully once -> reruns are idempotent
CREATE UNIQUE INDEX UX_file_load_hash_loaded ON ops.file_load(file_hash) WHERE status = 'loaded';

/* ------------------------------------------------------------- stg
   Every source column is NVARCHAR(100) so a bad value never breaks the load.
   Typing and validation happen in the DQ step, where failures are logged. */
CREATE TABLE stg.climate_reading (
    load_id       INT           NOT NULL,
    source_file   NVARCHAR(260) NOT NULL,
    source_row    INT           NOT NULL,
    zone_id       NVARCHAR(100) NULL,
    sensor_id     NVARCHAR(100) NULL,
    reading_ts    NVARCHAR(100) NULL,
    temp_c        NVARCHAR(100) NULL,
    humidity_pct  NVARCHAR(100) NULL,
    co2_ppm       NVARCHAR(100) NULL,
    light_par     NVARCHAR(100) NULL,
    landed_at     DATETIME2(0)  NOT NULL CONSTRAINT DF_stg_climate_landed DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_stg_climate_reading PRIMARY KEY (load_id, source_row)
);

CREATE TABLE stg.harvest (
    load_id       INT           NOT NULL,
    source_file   NVARCHAR(260) NOT NULL,
    source_row    INT           NOT NULL,
    harvest_id    NVARCHAR(100) NULL,
    zone_id       NVARCHAR(100) NULL,
    crop_name     NVARCHAR(100) NULL,
    harvest_date  NVARCHAR(100) NULL,
    grade         NVARCHAR(100) NULL,
    kg_harvested  NVARCHAR(100) NULL,
    recorded_by   NVARCHAR(100) NULL,
    landed_at     DATETIME2(0)  NOT NULL CONSTRAINT DF_stg_harvest_landed DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_stg_harvest PRIMARY KEY (load_id, source_row)
);

CREATE TABLE stg.shipment_line (
    load_id        INT           NOT NULL,
    source_file    NVARCHAR(260) NOT NULL,
    source_row     INT           NOT NULL,     -- position of the line within the flattened JSON
    extract_date   NVARCHAR(100) NULL,
    shipment_id    NVARCHAR(100) NULL,
    customer_name  NVARCHAR(100) NULL,
    ship_date      NVARCHAR(100) NULL,
    line_no        NVARCHAR(100) NULL,
    sku            NVARCHAR(100) NULL,
    cases          NVARCHAR(100) NULL,
    landed_at      DATETIME2(0)  NOT NULL CONSTRAINT DF_stg_shipment_landed DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_stg_shipment_line PRIMARY KEY (load_id, source_row)
);

/* ------------------------------------------------------------- dq */
CREATE TABLE dq.check_rule (
    rule_id       VARCHAR(20)    NOT NULL CONSTRAINT PK_dq_check_rule PRIMARY KEY,
    rule_name     NVARCHAR(200)  NOT NULL,
    source_system VARCHAR(30)    NOT NULL,
    target_table  NVARCHAR(128)  NOT NULL,
    check_level   VARCHAR(10)    NOT NULL,       -- Row: tested per staging row. Set: tested across a whole table or day.
    dq_dimension  VARCHAR(20)    NOT NULL,
    severity      VARCHAR(10)    NOT NULL,
    on_fail       VARCHAR(12)    NOT NULL,       -- Quarantine: row is kept out of dw. Warn: row loads, issue is logged.
    description   NVARCHAR(1000) NULL,
    is_active     BIT            NOT NULL CONSTRAINT DF_dq_rule_active DEFAULT 1,
    CONSTRAINT CK_dq_rule_dimension CHECK (dq_dimension IN ('Completeness','Validity','Uniqueness','Consistency','Timeliness','Accuracy')),
    CONSTRAINT CK_dq_rule_severity  CHECK (severity IN ('Critical','High','Medium','Low')),
    CONSTRAINT CK_dq_rule_on_fail    CHECK (on_fail IN ('Quarantine','Warn')),
    CONSTRAINT CK_dq_rule_level      CHECK (check_level IN ('Row','Set')),
    CONSTRAINT CK_dq_rule_set_warns  CHECK (check_level = 'Row' OR on_fail = 'Warn')   -- set checks have no single row to quarantine
);

CREATE TABLE dq.result (
    result_id     BIGINT IDENTITY(1,1) CONSTRAINT PK_dq_result PRIMARY KEY,
    run_id        INT            NOT NULL CONSTRAINT FK_dq_result_run  REFERENCES ops.pipeline_run(run_id),
    rule_id       VARCHAR(20)    NOT NULL CONSTRAINT FK_dq_result_rule REFERENCES dq.check_rule(rule_id),
    checked_at    DATETIME2(0)   NOT NULL CONSTRAINT DF_dq_result_at DEFAULT SYSUTCDATETIME(),
    rows_checked  INT            NOT NULL,
    rows_failed   INT            NOT NULL,
    failure_rate  AS CAST(CASE WHEN rows_checked = 0 THEN 0 ELSE 1.0 * rows_failed / rows_checked END AS DECIMAL(9,6)) PERSISTED,
    status        AS CAST(CASE WHEN rows_failed = 0 THEN 'Pass' ELSE 'Fail' END AS VARCHAR(4)),
    details       NVARCHAR(4000) NULL
);

/* ------------------------------------------------------------- err */
CREATE TABLE err.quarantine (
    quarantine_id   BIGINT IDENTITY(1,1) CONSTRAINT PK_err_quarantine PRIMARY KEY,
    run_id          INT            NOT NULL,
    rule_id         VARCHAR(20)    NOT NULL,
    source_table    NVARCHAR(128)  NOT NULL,
    load_id         INT            NOT NULL,
    source_row      INT            NOT NULL,
    row_data        NVARCHAR(MAX)  NULL CONSTRAINT CK_quarantine_json CHECK (row_data IS NULL OR ISJSON(row_data) = 1),
    quarantined_at  DATETIME2(0)   NOT NULL CONSTRAINT DF_quarantine_at DEFAULT SYSUTCDATETIME(),
    resolved_at     DATETIME2(0)   NULL,
    resolution      NVARCHAR(400)  NULL
);

/* ------------------------------------------------------------- dw (star schema) */
CREATE TABLE dw.dim_date (
    date_key     INT          NOT NULL CONSTRAINT PK_dim_date PRIMARY KEY,   -- yyyymmdd
    full_date    DATE         NOT NULL CONSTRAINT UQ_dim_date UNIQUE,
    day_name     VARCHAR(10)  NOT NULL,
    day_of_week  TINYINT      NOT NULL,                                       -- 1 = Monday
    iso_week     TINYINT      NOT NULL,
    month_num    TINYINT      NOT NULL,
    month_name   VARCHAR(10)  NOT NULL,
    year_num     SMALLINT     NOT NULL
);

CREATE TABLE dw.dim_crop (
    crop_key     INT IDENTITY(1,1) CONSTRAINT PK_dim_crop PRIMARY KEY,
    crop_name    NVARCHAR(50) NOT NULL CONSTRAINT UQ_dim_crop UNIQUE,
    crop_family  NVARCHAR(30) NOT NULL
);

CREATE TABLE dw.dim_zone (
    zone_key     INT IDENTITY(1,1) CONSTRAINT PK_dim_zone PRIMARY KEY,
    zone_id      VARCHAR(10)  NOT NULL CONSTRAINT UQ_dim_zone UNIQUE,
    greenhouse   VARCHAR(10)  NOT NULL,
    crop_key     INT          NOT NULL CONSTRAINT FK_dim_zone_crop REFERENCES dw.dim_crop(crop_key),
    area_m2      INT          NOT NULL
);

CREATE TABLE dw.dim_sku (
    sku_key      INT IDENTITY(1,1) CONSTRAINT PK_dim_sku PRIMARY KEY,
    sku          VARCHAR(20)   NOT NULL CONSTRAINT UQ_dim_sku UNIQUE,
    description  NVARCHAR(100) NOT NULL,
    crop_key     INT           NOT NULL CONSTRAINT FK_dim_sku_crop REFERENCES dw.dim_crop(crop_key),
    case_kg      DECIMAL(6,2)  NOT NULL
);

CREATE TABLE dw.dim_customer (
    customer_key   INT IDENTITY(1,1) CONSTRAINT PK_dim_customer PRIMARY KEY,
    customer_name  NVARCHAR(100) NOT NULL CONSTRAINT UQ_dim_customer UNIQUE
);

CREATE TABLE dw.fact_climate_hourly (
    zone_key      INT          NOT NULL CONSTRAINT FK_fch_zone REFERENCES dw.dim_zone(zone_key),
    reading_ts    DATETIME2(0) NOT NULL,
    date_key      INT          NOT NULL CONSTRAINT FK_fch_date REFERENCES dw.dim_date(date_key),
    temp_c        DECIMAL(5,1) NULL,
    humidity_pct  DECIMAL(5,1) NULL,
    co2_ppm       INT          NULL,
    light_par     INT          NULL,
    load_id       INT          NOT NULL,
    CONSTRAINT PK_fact_climate_hourly PRIMARY KEY (zone_key, reading_ts)
);

CREATE TABLE dw.fact_harvest (
    harvest_id    INT           NOT NULL CONSTRAINT PK_fact_harvest PRIMARY KEY,
    date_key      INT           NOT NULL CONSTRAINT FK_fh_date REFERENCES dw.dim_date(date_key),
    zone_key      INT           NOT NULL CONSTRAINT FK_fh_zone REFERENCES dw.dim_zone(zone_key),
    crop_key      INT           NOT NULL CONSTRAINT FK_fh_crop REFERENCES dw.dim_crop(crop_key),
    grade         VARCHAR(4)    NOT NULL CONSTRAINT CK_fh_grade CHECK (grade IN ('A','B','Cull')),
    kg_harvested  DECIMAL(10,1) NOT NULL CONSTRAINT CK_fh_kg CHECK (kg_harvested >= 0),
    recorded_by   NVARCHAR(50)  NULL,
    load_id       INT           NOT NULL
);

CREATE TABLE dw.fact_shipment_line (
    shipment_id    VARCHAR(30)   NOT NULL,
    line_no        INT           NOT NULL,
    ship_date_key  INT           NOT NULL CONSTRAINT FK_fsl_date     REFERENCES dw.dim_date(date_key),
    customer_key   INT           NOT NULL CONSTRAINT FK_fsl_customer REFERENCES dw.dim_customer(customer_key),
    sku_key        INT           NOT NULL CONSTRAINT FK_fsl_sku      REFERENCES dw.dim_sku(sku_key),
    cases          INT           NOT NULL CONSTRAINT CK_fsl_cases CHECK (cases > 0),
    kg_shipped     DECIMAL(12,2) NOT NULL,
    load_id        INT           NOT NULL,
    CONSTRAINT PK_fact_shipment_line PRIMARY KEY (shipment_id, line_no)
);
