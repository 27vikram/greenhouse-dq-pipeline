/* 03_seed_config.sql — configuration and the date dimension */
SET NOCOUNT ON;

INSERT INTO ref.sensor_valid_range (metric, unit, min_valid, max_valid, note) VALUES
    ('temp_c',       N'°C',        -5,    45, N'Greenhouse air temperature'),
    ('humidity_pct', N'%',          0,   100, N'Relative humidity'),
    ('co2_ppm',      N'ppm',      250,  2000, N'CO2 including daytime enrichment'),
    ('light_par',    N'µmol/m²/s',  0,  2500, N'Photosynthetically active radiation');

-- dim_date: 2026-01-01 to 2027-12-31 (covers out-of-range typos so they are caught by DQ, not by an FK error)
WITH d AS (
    SELECT CAST('2026-01-01' AS DATE) AS dt
    UNION ALL
    SELECT DATEADD(DAY, 1, dt) FROM d WHERE dt < '2027-12-31'
)
INSERT INTO dw.dim_date (date_key, full_date, day_name, day_of_week, iso_week, month_num, month_name, year_num)
SELECT YEAR(dt) * 10000 + MONTH(dt) * 100 + DAY(dt),
       dt,
       DATENAME(WEEKDAY, dt),
       ((DATEPART(WEEKDAY, dt) + @@DATEFIRST - 2) % 7) + 1,   -- Monday = 1 regardless of server DATEFIRST
       DATEPART(ISO_WEEK, dt),
       MONTH(dt),
       DATENAME(MONTH, dt),
       YEAR(dt)
FROM d
OPTION (MAXRECURSION 800);
