/* =====================================================================
   05_dq_engine.sql — data quality engine and warehouse load

   ops.usp_process_pending @run_id          <- the one entry point, one transaction
     ├─ dw.usp_load_dimensions               ref.* -> dw.dim_*
     ├─ dw.usp_process_climate   @run_id     stg -> row rules -> err.quarantine | dw.fact_climate_hourly
     ├─ dw.usp_process_harvest   @run_id     stg -> row rules -> err.quarantine | dw.fact_harvest
     ├─ dw.usp_process_shipments @run_id     stg -> row rules -> err.quarantine | dw.fact_shipment_line
     └─ dq.usp_run_set_checks    @run_id     missing files, incomplete days, flatlines, reconciliation

   Each source procedure only picks up files not yet processed
   (ops.file_load.processed_run_id IS NULL), so loads are incremental.
   Every rule writes one row to dq.result per run: rows checked, rows failed.
   ===================================================================== */
SET NOCOUNT ON;
GO

/* ------------------------------------------------------------- helpers */
-- Date embedded in a file name, e.g. climate_20260915.csv -> 2026-09-15
CREATE OR ALTER FUNCTION ops.fn_file_date (@file_name NVARCHAR(260))
RETURNS DATE
AS
BEGIN
    DECLARE @pos INT = PATINDEX(N'%[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]%', @file_name);
    RETURN CASE WHEN @pos > 0 THEN TRY_CONVERT(DATE, SUBSTRING(@file_name, @pos, 8), 112) END;
END;
GO

CREATE OR ALTER FUNCTION dq.fn_rule_active (@rule_id VARCHAR(20))
RETURNS BIT
AS
BEGIN
    RETURN CASE WHEN EXISTS (SELECT 1 FROM dq.check_rule WHERE rule_id = @rule_id AND is_active = 1) THEN 1 ELSE 0 END;
END;
GO

/* ------------------------------------------------------------- views */
-- Every rule result with its rule metadata: the main dashboard source
CREATE OR ALTER VIEW dq.vw_rule_results
AS
SELECT r.result_id, r.run_id, pr.started_at AS run_started_at,
       r.rule_id, c.rule_name, c.source_system, c.check_level, c.dq_dimension, c.severity, c.on_fail,
       r.rows_checked, r.rows_failed, r.failure_rate, r.status, r.details
FROM dq.result r
JOIN dq.check_rule c      ON c.rule_id = r.rule_id
JOIN ops.pipeline_run pr  ON pr.run_id = r.run_id;
GO

-- Most recent result for each rule
CREATE OR ALTER VIEW dq.vw_latest_rule_status
AS
SELECT result_id, run_id, run_started_at, rule_id, rule_name, source_system, check_level, dq_dimension,
       severity, on_fail, rows_checked, rows_failed, failure_rate, status, details
FROM (
    SELECT v.*, ROW_NUMBER() OVER (PARTITION BY v.rule_id ORDER BY v.result_id DESC) AS rn
    FROM dq.vw_rule_results v
) x
WHERE rn = 1;
GO

-- Harvest vs shipments per crop: the reconciliation behind REC-001 and the RCA
CREATE OR ALTER VIEW dq.vw_crop_reconciliation
AS
WITH h AS (
    SELECT crop_key, SUM(kg_harvested) AS kg
    FROM dw.fact_harvest
    WHERE grade IN ('A', 'B')                       -- Cull is never shipped
    GROUP BY crop_key
), s AS (
    SELECT k.crop_key, SUM(f.kg_shipped) AS kg
    FROM dw.fact_shipment_line f
    JOIN dw.dim_sku k ON k.sku_key = f.sku_key
    GROUP BY k.crop_key
)
SELECT c.crop_name,
       CAST(ISNULL(h.kg, 0) AS DECIMAL(14,1))                AS sellable_kg,
       CAST(ISNULL(s.kg, 0) AS DECIMAL(14,1))                AS shipped_kg,
       CAST(ISNULL(s.kg, 0) / NULLIF(h.kg, 0) AS DECIMAL(9,3)) AS shipped_to_harvest_ratio
FROM dw.dim_crop c
LEFT JOIN h ON h.crop_key = c.crop_key
LEFT JOIN s ON s.crop_key = c.crop_key;
GO

-- Quarantined rows grouped for triage
CREATE OR ALTER VIEW err.vw_quarantine_summary
AS
SELECT q.rule_id, c.rule_name, q.source_table, fl.file_name,
       COUNT(*) AS rows_quarantined,
       SUM(CASE WHEN q.resolved_at IS NULL THEN 1 ELSE 0 END) AS rows_open
FROM err.quarantine q
JOIN dq.check_rule c   ON c.rule_id = q.rule_id
JOIN ops.file_load fl  ON fl.load_id = q.load_id
GROUP BY q.rule_id, c.rule_name, q.source_table, fl.file_name;
GO

/* ------------------------------------------------------------- dimensions */
CREATE OR ALTER PROCEDURE dw.usp_load_dimensions
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    -- insert new members; type 1 update of changed attributes
    INSERT INTO dw.dim_crop (crop_name, crop_family)
    SELECT c.crop_name, c.crop_family
    FROM ref.crop c
    WHERE NOT EXISTS (SELECT 1 FROM dw.dim_crop d WHERE d.crop_name = c.crop_name);

    UPDATE d SET d.crop_family = c.crop_family
    FROM dw.dim_crop d JOIN ref.crop c ON c.crop_name = d.crop_name
    WHERE d.crop_family <> c.crop_family;

    INSERT INTO dw.dim_zone (zone_id, greenhouse, crop_key, area_m2)
    SELECT z.zone_id, z.greenhouse, dc.crop_key, z.area_m2
    FROM ref.zone z
    JOIN dw.dim_crop dc ON dc.crop_name = z.crop_name
    WHERE NOT EXISTS (SELECT 1 FROM dw.dim_zone d WHERE d.zone_id = z.zone_id);

    UPDATE d SET d.greenhouse = z.greenhouse, d.crop_key = dc.crop_key, d.area_m2 = z.area_m2
    FROM dw.dim_zone d
    JOIN ref.zone z     ON z.zone_id = d.zone_id
    JOIN dw.dim_crop dc ON dc.crop_name = z.crop_name
    WHERE d.greenhouse <> z.greenhouse OR d.crop_key <> dc.crop_key OR d.area_m2 <> z.area_m2;

    INSERT INTO dw.dim_sku (sku, description, crop_key, case_kg)
    SELECT k.sku, k.description, dc.crop_key, k.case_kg
    FROM ref.sku k
    JOIN dw.dim_crop dc ON dc.crop_name = k.crop_name
    WHERE NOT EXISTS (SELECT 1 FROM dw.dim_sku d WHERE d.sku = k.sku);

    UPDATE d SET d.description = k.description, d.crop_key = dc.crop_key, d.case_kg = k.case_kg
    FROM dw.dim_sku d
    JOIN ref.sku k      ON k.sku = d.sku
    JOIN dw.dim_crop dc ON dc.crop_name = k.crop_name
    WHERE d.description <> k.description OR d.crop_key <> dc.crop_key OR d.case_kg <> k.case_kg;
END;
GO

/* ------------------------------------------------------------- climate */
CREATE OR ALTER PROCEDURE dw.usp_process_climate
    @run_id INT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    SELECT load_id INTO #loads
    FROM ops.file_load
    WHERE source_system = 'climate' AND status = 'loaded' AND processed_run_id IS NULL;

    IF NOT EXISTS (SELECT 1 FROM #loads) RETURN;

    DECLARE @tmin DECIMAL(9,2), @tmax DECIMAL(9,2), @hmin DECIMAL(9,2), @hmax DECIMAL(9,2),
            @cmin DECIMAL(9,2), @cmax DECIMAL(9,2), @lmin DECIMAL(9,2), @lmax DECIMAL(9,2);
    SELECT @tmin = min_valid, @tmax = max_valid FROM ref.sensor_valid_range WHERE metric = 'temp_c';
    SELECT @hmin = min_valid, @hmax = max_valid FROM ref.sensor_valid_range WHERE metric = 'humidity_pct';
    SELECT @cmin = min_valid, @cmax = max_valid FROM ref.sensor_valid_range WHERE metric = 'co2_ppm';
    SELECT @lmin = min_valid, @lmax = max_valid FROM ref.sensor_valid_range WHERE metric = 'light_par';

    -- typed copy of pending rows: TRY_CONVERT gives NULL for bad values instead of failing the batch
    SELECT s.load_id, s.source_row,
           z.zone_id,
           TRY_CONVERT(DATETIME2(0), s.reading_ts, 120) AS reading_ts,
           TRY_CONVERT(DECIMAL(9,2), s.temp_c)          AS temp_c,
           TRY_CONVERT(DECIMAL(9,2), s.humidity_pct)    AS humidity_pct,
           TRY_CONVERT(DECIMAL(9,2), s.co2_ppm)         AS co2_ppm,
           TRY_CONVERT(DECIMAL(9,2), s.light_par)       AS light_par
    INTO #c
    FROM stg.climate_reading s
    JOIN #loads l        ON l.load_id = s.load_id
    LEFT JOIN ref.zone z ON z.zone_id = s.zone_id;

    CREATE TABLE #fail (load_id INT NOT NULL, source_row INT NOT NULL, rule_id VARCHAR(20) COLLATE DATABASE_DEFAULT NOT NULL);

    INSERT INTO #fail SELECT load_id, source_row, 'CLM-001' FROM #c WHERE zone_id IS NULL;

    INSERT INTO #fail SELECT c.load_id, c.source_row, 'CLM-002' FROM #c c
    WHERE c.reading_ts IS NULL
       OR NOT EXISTS (SELECT 1 FROM dw.dim_date d WHERE d.full_date = CAST(c.reading_ts AS DATE));

    INSERT INTO #fail
    SELECT load_id, source_row, 'CLM-003'
    FROM (
        SELECT c.load_id, c.source_row,
               ROW_NUMBER() OVER (PARTITION BY c.zone_id, c.reading_ts ORDER BY c.load_id, c.source_row) AS copy_no,
               CASE WHEN EXISTS (SELECT 1
                                 FROM dw.fact_climate_hourly f
                                 JOIN dw.dim_zone dz ON dz.zone_key = f.zone_key
                                 WHERE dz.zone_id = c.zone_id AND f.reading_ts = c.reading_ts)
                    THEN 1 ELSE 0 END AS already_in_dw
        FROM #c c
        WHERE c.zone_id IS NOT NULL AND c.reading_ts IS NOT NULL
    ) d
    WHERE copy_no > 1 OR already_in_dw = 1;

    INSERT INTO #fail SELECT load_id, source_row, 'CLM-004' FROM #c WHERE temp_c       IS NULL OR temp_c       NOT BETWEEN @tmin AND @tmax;
    INSERT INTO #fail SELECT load_id, source_row, 'CLM-005' FROM #c WHERE humidity_pct IS NULL OR humidity_pct NOT BETWEEN @hmin AND @hmax;
    INSERT INTO #fail SELECT load_id, source_row, 'CLM-006' FROM #c WHERE co2_ppm      IS NULL OR co2_ppm      NOT BETWEEN @cmin AND @cmax;
    INSERT INTO #fail SELECT load_id, source_row, 'CLM-007' FROM #c WHERE light_par    IS NULL OR light_par    NOT BETWEEN @lmin AND @lmax;

    DELETE f FROM #fail f JOIN dq.check_rule r ON r.rule_id = f.rule_id WHERE r.is_active = 0;

    -- 1. one result row per active row-level rule
    DECLARE @checked INT = (SELECT COUNT(*) FROM #c);
    INSERT INTO dq.result (run_id, rule_id, rows_checked, rows_failed)
    SELECT @run_id, r.rule_id, @checked, COUNT(f.rule_id)
    FROM dq.check_rule r
    LEFT JOIN #fail f ON f.rule_id = r.rule_id
    WHERE r.target_table = N'stg.climate_reading' AND r.check_level = 'Row' AND r.is_active = 1
    GROUP BY r.rule_id;

    -- 2. quarantine: the raw staging row as JSON, once per rule it failed
    INSERT INTO err.quarantine (run_id, rule_id, source_table, load_id, source_row, row_data)
    SELECT @run_id, f.rule_id, N'stg.climate_reading', s.load_id, s.source_row,
           (SELECT s.source_file, s.source_row, s.zone_id, s.sensor_id, s.reading_ts,
                   s.temp_c, s.humidity_pct, s.co2_ppm, s.light_par
            FOR JSON PATH, WITHOUT_ARRAY_WRAPPER, INCLUDE_NULL_VALUES)
    FROM #fail f
    JOIN dq.check_rule r          ON r.rule_id = f.rule_id AND r.on_fail = 'Quarantine'
    JOIN stg.climate_reading s    ON s.load_id = f.load_id AND s.source_row = f.source_row;

    -- 3. load rows that passed every quarantine rule
    INSERT INTO dw.fact_climate_hourly (zone_key, reading_ts, date_key, temp_c, humidity_pct, co2_ppm, light_par, load_id)
    SELECT dz.zone_key, c.reading_ts,
           YEAR(c.reading_ts) * 10000 + MONTH(c.reading_ts) * 100 + DAY(c.reading_ts),
           c.temp_c, c.humidity_pct, CAST(c.co2_ppm AS INT), CAST(c.light_par AS INT), c.load_id
    FROM #c c
    JOIN dw.dim_zone dz ON dz.zone_id = c.zone_id
    WHERE NOT EXISTS (SELECT 1
                      FROM #fail f
                      JOIN dq.check_rule r ON r.rule_id = f.rule_id AND r.on_fail = 'Quarantine'
                      WHERE f.load_id = c.load_id AND f.source_row = c.source_row);

    UPDATE ops.file_load SET processed_run_id = @run_id WHERE load_id IN (SELECT load_id FROM #loads);
END;
GO

/* ------------------------------------------------------------- harvest */
CREATE OR ALTER PROCEDURE dw.usp_process_harvest
    @run_id INT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    SELECT load_id INTO #loads
    FROM ops.file_load
    WHERE source_system = 'erp_harvest' AND status = 'loaded' AND processed_run_id IS NULL;

    IF NOT EXISTS (SELECT 1 FROM #loads) RETURN;

    SELECT s.load_id, s.source_row,
           TRY_CONVERT(INT, s.harvest_id)             AS harvest_id,
           z.zone_id,
           z.crop_name                                AS zone_crop,
           COALESCE(rc.crop_name, a.crop_name)        AS crop_name,     -- master spelling, or alias mapped to it
           TRY_CONVERT(DATE, s.harvest_date, 23)      AS harvest_date,
           ops.fn_file_date(s.source_file)            AS extract_date,
           CASE UPPER(LTRIM(RTRIM(s.grade))) WHEN 'A' THEN 'A' WHEN 'B' THEN 'B' WHEN 'CULL' THEN 'Cull' END AS grade,
           TRY_CONVERT(DECIMAL(10,1), s.kg_harvested) AS kg_harvested,
           LEFT(s.recorded_by, 50)                    AS recorded_by
    INTO #h
    FROM stg.harvest s
    JOIN #loads l              ON l.load_id = s.load_id
    LEFT JOIN ref.zone z       ON z.zone_id = s.zone_id
    LEFT JOIN ref.crop rc      ON rc.crop_name = s.crop_name
    LEFT JOIN ref.crop_alias a ON a.alias = s.crop_name;

    CREATE TABLE #fail (load_id INT NOT NULL, source_row INT NOT NULL, rule_id VARCHAR(20) COLLATE DATABASE_DEFAULT NOT NULL);

    INSERT INTO #fail SELECT load_id, source_row, 'HRV-001' FROM #h WHERE harvest_id IS NULL;

    INSERT INTO #fail SELECT h.load_id, h.source_row, 'HRV-002' FROM #h h
    WHERE h.harvest_id IN (SELECT harvest_id FROM #h WHERE harvest_id IS NOT NULL GROUP BY harvest_id HAVING COUNT(*) > 1)
       OR EXISTS (SELECT 1 FROM dw.fact_harvest f WHERE f.harvest_id = h.harvest_id);

    INSERT INTO #fail SELECT load_id, source_row, 'HRV-003' FROM #h WHERE zone_id IS NULL;
    INSERT INTO #fail SELECT load_id, source_row, 'HRV-004' FROM #h WHERE crop_name IS NULL;

    INSERT INTO #fail SELECT h.load_id, h.source_row, 'HRV-005' FROM #h h
    WHERE h.harvest_date IS NULL
       OR h.harvest_date > COALESCE(h.extract_date, '9999-12-31')
       OR NOT EXISTS (SELECT 1 FROM dw.dim_date d WHERE d.full_date = h.harvest_date);

    INSERT INTO #fail SELECT load_id, source_row, 'HRV-006' FROM #h WHERE grade IS NULL;
    INSERT INTO #fail SELECT load_id, source_row, 'HRV-007' FROM #h WHERE kg_harvested IS NULL OR kg_harvested < 0;
    INSERT INTO #fail SELECT load_id, source_row, 'HRV-008' FROM #h
    WHERE zone_crop IS NOT NULL AND crop_name IS NOT NULL AND crop_name <> zone_crop;

    DELETE f FROM #fail f JOIN dq.check_rule r ON r.rule_id = f.rule_id WHERE r.is_active = 0;

    DECLARE @checked INT = (SELECT COUNT(*) FROM #h);
    INSERT INTO dq.result (run_id, rule_id, rows_checked, rows_failed)
    SELECT @run_id, r.rule_id, @checked, COUNT(f.rule_id)
    FROM dq.check_rule r
    LEFT JOIN #fail f ON f.rule_id = r.rule_id
    WHERE r.target_table = N'stg.harvest' AND r.check_level = 'Row' AND r.is_active = 1
    GROUP BY r.rule_id;

    INSERT INTO err.quarantine (run_id, rule_id, source_table, load_id, source_row, row_data)
    SELECT @run_id, f.rule_id, N'stg.harvest', s.load_id, s.source_row,
           (SELECT s.source_file, s.source_row, s.harvest_id, s.zone_id, s.crop_name, s.harvest_date,
                   s.grade, s.kg_harvested, s.recorded_by
            FOR JSON PATH, WITHOUT_ARRAY_WRAPPER, INCLUDE_NULL_VALUES)
    FROM #fail f
    JOIN dq.check_rule r ON r.rule_id = f.rule_id AND r.on_fail = 'Quarantine'
    JOIN stg.harvest s   ON s.load_id = f.load_id AND s.source_row = f.source_row;

    INSERT INTO dw.fact_harvest (harvest_id, date_key, zone_key, crop_key, grade, kg_harvested, recorded_by, load_id)
    SELECT h.harvest_id,
           YEAR(h.harvest_date) * 10000 + MONTH(h.harvest_date) * 100 + DAY(h.harvest_date),
           dz.zone_key, dc.crop_key, h.grade, h.kg_harvested, h.recorded_by, h.load_id
    FROM #h h
    JOIN dw.dim_zone dz ON dz.zone_id = h.zone_id
    JOIN dw.dim_crop dc ON dc.crop_name = h.crop_name
    WHERE NOT EXISTS (SELECT 1
                      FROM #fail f
                      JOIN dq.check_rule r ON r.rule_id = f.rule_id AND r.on_fail = 'Quarantine'
                      WHERE f.load_id = h.load_id AND f.source_row = h.source_row);

    UPDATE ops.file_load SET processed_run_id = @run_id WHERE load_id IN (SELECT load_id FROM #loads);
END;
GO

/* ------------------------------------------------------------- shipments */
CREATE OR ALTER PROCEDURE dw.usp_process_shipments
    @run_id INT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    SELECT load_id INTO #loads
    FROM ops.file_load
    WHERE source_system = 'wms_shipments' AND status = 'loaded' AND processed_run_id IS NULL;

    IF NOT EXISTS (SELECT 1 FROM #loads) RETURN;

    SELECT s.load_id, s.source_row,
           CASE WHEN LEN(s.shipment_id) <= 30 THEN CAST(s.shipment_id AS VARCHAR(30)) END AS shipment_id,
           TRY_CONVERT(INT, s.line_no)           AS line_no,
           s.customer_name,
           TRY_CONVERT(DATE, s.ship_date, 23)    AS ship_date,
           TRY_CONVERT(DATE, s.extract_date, 23) AS extract_date,
           k.sku,
           k.case_kg,
           TRY_CONVERT(INT, s.cases)             AS cases
    INTO #s
    FROM stg.shipment_line s
    JOIN #loads l       ON l.load_id = s.load_id
    LEFT JOIN ref.sku k ON k.sku = s.sku;

    CREATE TABLE #fail (load_id INT NOT NULL, source_row INT NOT NULL, rule_id VARCHAR(20) COLLATE DATABASE_DEFAULT NOT NULL);

    INSERT INTO #fail SELECT load_id, source_row, 'SHP-001' FROM #s WHERE shipment_id IS NULL OR line_no IS NULL;

    INSERT INTO #fail SELECT x.load_id, x.source_row, 'SHP-002' FROM #s x
    WHERE x.ship_date IS NULL
       OR x.ship_date > COALESCE(x.extract_date, '9999-12-31')
       OR NOT EXISTS (SELECT 1 FROM dw.dim_date d WHERE d.full_date = x.ship_date);

    INSERT INTO #fail
    SELECT load_id, source_row, 'SHP-003'
    FROM (
        SELECT x.load_id, x.source_row,
               ROW_NUMBER() OVER (PARTITION BY x.shipment_id, x.line_no ORDER BY x.load_id, x.source_row) AS copy_no,
               CASE WHEN EXISTS (SELECT 1 FROM dw.fact_shipment_line f
                                 WHERE f.shipment_id = x.shipment_id AND f.line_no = x.line_no)
                    THEN 1 ELSE 0 END AS already_in_dw
        FROM #s x
        WHERE x.shipment_id IS NOT NULL AND x.line_no IS NOT NULL
    ) d
    WHERE copy_no > 1 OR already_in_dw = 1;

    INSERT INTO #fail SELECT load_id, source_row, 'SHP-004' FROM #s WHERE sku IS NULL;
    INSERT INTO #fail SELECT load_id, source_row, 'SHP-005' FROM #s WHERE cases IS NULL OR cases <= 0;
    INSERT INTO #fail SELECT load_id, source_row, 'SHP-006' FROM #s WHERE customer_name IS NULL;
    INSERT INTO #fail SELECT load_id, source_row, 'SHP-102' FROM #s WHERE ship_date < extract_date;

    DELETE f FROM #fail f JOIN dq.check_rule r ON r.rule_id = f.rule_id WHERE r.is_active = 0;

    DECLARE @checked INT = (SELECT COUNT(*) FROM #s);
    INSERT INTO dq.result (run_id, rule_id, rows_checked, rows_failed)
    SELECT @run_id, r.rule_id, @checked, COUNT(f.rule_id)
    FROM dq.check_rule r
    LEFT JOIN #fail f ON f.rule_id = r.rule_id
    WHERE r.target_table = N'stg.shipment_line' AND r.check_level = 'Row' AND r.is_active = 1
    GROUP BY r.rule_id;

    INSERT INTO err.quarantine (run_id, rule_id, source_table, load_id, source_row, row_data)
    SELECT @run_id, f.rule_id, N'stg.shipment_line', s.load_id, s.source_row,
           (SELECT s.source_file, s.source_row, s.extract_date, s.shipment_id, s.customer_name,
                   s.ship_date, s.line_no, s.sku, s.cases
            FOR JSON PATH, WITHOUT_ARRAY_WRAPPER, INCLUDE_NULL_VALUES)
    FROM #fail f
    JOIN dq.check_rule r       ON r.rule_id = f.rule_id AND r.on_fail = 'Quarantine'
    JOIN stg.shipment_line s   ON s.load_id = f.load_id AND s.source_row = f.source_row;

    -- rows that passed every quarantine rule
    SELECT x.* INTO #ok
    FROM #s x
    WHERE NOT EXISTS (SELECT 1
                      FROM #fail f
                      JOIN dq.check_rule r ON r.rule_id = f.rule_id AND r.on_fail = 'Quarantine'
                      WHERE f.load_id = x.load_id AND f.source_row = x.source_row);

    INSERT INTO dw.dim_customer (customer_name)
    SELECT DISTINCT o.customer_name
    FROM #ok o
    WHERE NOT EXISTS (SELECT 1 FROM dw.dim_customer c WHERE c.customer_name = o.customer_name);

    INSERT INTO dw.fact_shipment_line (shipment_id, line_no, ship_date_key, customer_key, sku_key, cases, kg_shipped, load_id)
    SELECT o.shipment_id, o.line_no,
           YEAR(o.ship_date) * 10000 + MONTH(o.ship_date) * 100 + DAY(o.ship_date),
           dc.customer_key, dk.sku_key, o.cases, o.cases * o.case_kg, o.load_id
    FROM #ok o
    JOIN dw.dim_customer dc ON dc.customer_name = o.customer_name
    JOIN dw.dim_sku dk      ON dk.sku = o.sku;

    UPDATE ops.file_load SET processed_run_id = @run_id WHERE load_id IN (SELECT load_id FROM #loads);
END;
GO

/* ------------------------------------------------------------- set-level checks */
CREATE OR ALTER PROCEDURE dq.usp_run_set_checks
    @run_id          INT,
    @flatline_hours  INT          = 6,      -- CLM-103: identical consecutive readings that count as stuck
    @rec_tolerance   DECIMAL(5,2) = 0.20    -- REC-001: shipped may exceed sellable harvest by this share
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    DECLARE @co2_rows INT;

    -- CLM-101 / SHP-101: calendar days with no file, between the first and last file received
    WITH files AS (
        SELECT DISTINCT source_system, ops.fn_file_date(file_name) AS file_date
        FROM ops.file_load
        WHERE status = 'loaded' AND source_system IN ('climate', 'wms_shipments')
    ), span AS (
        SELECT source_system, MIN(file_date) AS d0, MAX(file_date) AS d1
        FROM files WHERE file_date IS NOT NULL
        GROUP BY source_system
    ), rules AS (
        SELECT v.rule_id, v.source_system
        FROM (VALUES ('CLM-101', 'climate'), ('SHP-101', 'wms_shipments')) v(rule_id, source_system)
        WHERE dq.fn_rule_active(v.rule_id) = 1
    )
    INSERT INTO dq.result (run_id, rule_id, rows_checked, rows_failed, details)
    SELECT @run_id, r.rule_id, COUNT(*),
           SUM(CASE WHEN f.file_date IS NULL THEN 1 ELSE 0 END),
           LEFT(STRING_AGG(CASE WHEN f.file_date IS NULL
                                THEN CAST(CONCAT(N'missing ', CONVERT(CHAR(10), d.full_date, 23)) AS NVARCHAR(MAX)) END, N', '), 4000)
    FROM rules r
    JOIN span s         ON s.source_system = r.source_system
    JOIN dw.dim_date d  ON d.full_date BETWEEN s.d0 AND s.d1
    LEFT JOIN files f   ON f.source_system = r.source_system AND f.file_date = d.full_date
    GROUP BY r.rule_id;

    -- CLM-102: every zone needs 24 distinct hourly readings on every delivered day (measured on the source data)
    IF dq.fn_rule_active('CLM-102') = 1
    BEGIN
        WITH days AS (
            SELECT DISTINCT ops.fn_file_date(file_name) AS d
            FROM ops.file_load
            WHERE status = 'loaded' AND source_system = 'climate'
        ), readings AS (
            SELECT s.zone_id, TRY_CONVERT(DATETIME2(0), s.reading_ts, 120) AS ts
            FROM stg.climate_reading s
            JOIN ops.file_load fl ON fl.load_id = s.load_id AND fl.status = 'loaded'
        ), counts AS (
            SELECT zone_id, CAST(ts AS DATE) AS d, COUNT(DISTINCT ts) AS n
            FROM readings
            WHERE ts IS NOT NULL
            GROUP BY zone_id, CAST(ts AS DATE)
        )
        INSERT INTO dq.result (run_id, rule_id, rows_checked, rows_failed, details)
        SELECT @run_id, 'CLM-102', COUNT(*),
               ISNULL(SUM(CASE WHEN ISNULL(c.n, 0) < 24 THEN 1 ELSE 0 END), 0),
               LEFT(STRING_AGG(CASE WHEN ISNULL(c.n, 0) < 24
                                    THEN CAST(CONCAT(z.zone_id, N' ', CONVERT(CHAR(10), dy.d, 23), N' has ', ISNULL(c.n, 0), N'/24') AS NVARCHAR(MAX)) END, N', '), 4000)
        FROM days dy
        CROSS JOIN ref.zone z
        LEFT JOIN counts c ON c.zone_id = z.zone_id AND c.d = dy.d
        WHERE dy.d IS NOT NULL;
    END;

    -- CLM-103: flatlined CO2 sensor (gaps-and-islands: same value for N+ consecutive readings)
    IF dq.fn_rule_active('CLM-103') = 1
    BEGIN
        SET @co2_rows = (SELECT COUNT(*) FROM dw.fact_climate_hourly WHERE co2_ppm IS NOT NULL);

        WITH x AS (
            SELECT zone_key, reading_ts, co2_ppm,
                   ROW_NUMBER() OVER (PARTITION BY zone_key ORDER BY reading_ts)
                 - ROW_NUMBER() OVER (PARTITION BY zone_key, co2_ppm ORDER BY reading_ts) AS island
            FROM dw.fact_climate_hourly
            WHERE co2_ppm IS NOT NULL
        ), runs AS (
            SELECT zone_key, co2_ppm, island, COUNT(*) AS n, MIN(reading_ts) AS t0
            FROM x
            GROUP BY zone_key, co2_ppm, island
        )
        INSERT INTO dq.result (run_id, rule_id, rows_checked, rows_failed, details)
        SELECT @run_id, 'CLM-103', @co2_rows,
               ISNULL(SUM(r.n), 0),
               LEFT(STRING_AGG(CAST(CONCAT(z.zone_id, N' stuck at ', r.co2_ppm, N' ppm for ', r.n,
                                           N'h from ', CONVERT(CHAR(16), r.t0, 120)) AS NVARCHAR(MAX)), N'; '), 4000)
        FROM runs r
        JOIN dw.dim_zone z ON z.zone_key = r.zone_key
        WHERE r.n >= @flatline_hours;
    END;

    -- REC-001: per crop, shipped kg must not exceed grade A+B harvested kg by more than the tolerance
    IF dq.fn_rule_active('REC-001') = 1
    BEGIN
        INSERT INTO dq.result (run_id, rule_id, rows_checked, rows_failed, details)
        SELECT @run_id, 'REC-001', COUNT(*),
               ISNULL(SUM(CASE WHEN v.shipped_kg > v.sellable_kg * (1 + @rec_tolerance) THEN 1 ELSE 0 END), 0),
               LEFT(STRING_AGG(CASE WHEN v.shipped_kg > v.sellable_kg * (1 + @rec_tolerance)
                                    THEN CAST(CONCAT(v.crop_name, N': shipped ', FORMAT(v.shipped_kg, 'N0'),
                                                     N' kg vs ', FORMAT(v.sellable_kg, 'N0'), N' kg harvested (',
                                                     v.shipped_to_harvest_ratio, N'x)') AS NVARCHAR(MAX)) END, N'; '), 4000)
        FROM dq.vw_crop_reconciliation v;
    END;
END;
GO

/* ------------------------------------------------------------- entry point */
CREATE OR ALTER PROCEDURE ops.usp_process_pending
    @run_id INT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;   -- any error rolls back the whole step: no half-loaded warehouse

    BEGIN TRANSACTION;
        EXEC dw.usp_load_dimensions;
        EXEC dw.usp_process_climate   @run_id = @run_id;
        EXEC dw.usp_process_harvest   @run_id = @run_id;
        EXEC dw.usp_process_shipments @run_id = @run_id;
        EXEC dq.usp_run_set_checks    @run_id = @run_id;
    COMMIT TRANSACTION;
END;
GO
