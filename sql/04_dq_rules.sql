/* 04_dq_rules.sql — the data quality rule catalogue
   Rule IDs: <source>-0xx = row-level rules run on each staging row
             <source>-1xx = set-level rules run across a day, file set or table
   on_fail:  Quarantine = row is kept out of dw and copied to err.quarantine
             Warn       = row still loads; the failure is logged in dq.result
   Turning a rule off (is_active = 0) needs no code change. */
SET NOCOUNT ON;

INSERT INTO dq.check_rule (rule_id, rule_name, source_system, target_table, check_level, dq_dimension, severity, on_fail, description) VALUES
-- climate sensors ----------------------------------------------------------------------------------
('CLM-001', N'Zone exists in zone master',                 'climate', N'stg.climate_reading', 'Row', 'Consistency',  'Critical', 'Quarantine', N'zone_id must match ref.zone.'),
('CLM-002', N'Reading timestamp is valid',                 'climate', N'stg.climate_reading', 'Row', 'Validity',     'Critical', 'Quarantine', N'reading_ts must parse as yyyy-mm-dd hh:mi:ss and fall inside the date dimension.'),
('CLM-003', N'One reading per zone per hour',              'climate', N'stg.climate_reading', 'Row', 'Uniqueness',   'High',     'Quarantine', N'Duplicate zone_id + reading_ts: the first copy loads, later copies (or copies of readings already in dw) are quarantined.'),
('CLM-004', N'Temperature within valid range',             'climate', N'stg.climate_reading', 'Row', 'Validity',     'High',     'Quarantine', N'temp_c must be numeric and within ref.sensor_valid_range.'),
('CLM-005', N'Humidity within valid range',                'climate', N'stg.climate_reading', 'Row', 'Validity',     'High',     'Quarantine', N'humidity_pct must be numeric and within ref.sensor_valid_range.'),
('CLM-006', N'CO2 within valid range',                     'climate', N'stg.climate_reading', 'Row', 'Validity',     'Medium',   'Quarantine', N'co2_ppm must be numeric and within ref.sensor_valid_range.'),
('CLM-007', N'Light within valid range',                   'climate', N'stg.climate_reading', 'Row', 'Validity',     'Medium',   'Quarantine', N'light_par must be numeric and within ref.sensor_valid_range.'),
('CLM-101', N'One climate file per day',                   'climate', N'ops.file_load',       'Set', 'Timeliness',   'High',     'Warn',       N'Every calendar day between the first and last delivered file must have a file. Fails = missing days.'),
('CLM-102', N'24 readings per zone per day',               'climate', N'stg.climate_reading', 'Set', 'Completeness', 'Medium',   'Warn',       N'For each delivered day, every zone needs 24 distinct hourly readings. Fails = incomplete zone-days.'),
('CLM-103', N'Sensor is not flatlined',                    'climate', N'dw.fact_climate_hourly','Set','Accuracy',    'Medium',   'Warn',       N'The same CO2 value for 6+ consecutive hours means a stuck sensor. These readings pass range checks. Fails = readings in a flatline run.'),
-- ERP harvest --------------------------------------------------------------------------------------
('HRV-001', N'Harvest ID is a whole number',               'erp_harvest', N'stg.harvest', 'Row', 'Validity',     'Critical', 'Quarantine', N'harvest_id must convert to INT.'),
('HRV-002', N'Harvest ID is unique',                       'erp_harvest', N'stg.harvest', 'Row', 'Uniqueness',   'Critical', 'Quarantine', N'All copies of a repeated harvest_id are quarantined, since there is no way to know which version is correct. Also catches IDs already in dw.'),
('HRV-003', N'Zone exists in zone master',                 'erp_harvest', N'stg.harvest', 'Row', 'Consistency',  'High',     'Quarantine', N'zone_id must match ref.zone.'),
('HRV-004', N'Crop name matches crop master',              'erp_harvest', N'stg.harvest', 'Row', 'Consistency',  'High',     'Quarantine', N'crop_name must match ref.crop or a mapped spelling in ref.crop_alias. Case and trailing spaces are ignored by the database collation.'),
('HRV-005', N'Harvest date is valid and not in the future','erp_harvest', N'stg.harvest', 'Row', 'Validity',     'High',     'Quarantine', N'harvest_date must parse as yyyy-mm-dd and be on or before the extract date in the file name.'),
('HRV-006', N'Grade is A, B or Cull',                      'erp_harvest', N'stg.harvest', 'Row', 'Completeness', 'High',     'Quarantine', N'grade must be present and one of A, B, Cull (any case).'),
('HRV-007', N'Harvested kg is a non-negative number',      'erp_harvest', N'stg.harvest', 'Row', 'Validity',     'High',     'Quarantine', N'kg_harvested must be numeric and >= 0.'),
('HRV-008', N'Crop matches the crop planted in the zone',  'erp_harvest', N'stg.harvest', 'Row', 'Consistency',  'Medium',   'Warn',       N'The resolved crop should equal the crop ref.zone says is planted there.'),
-- WMS shipments ------------------------------------------------------------------------------------
('SHP-001', N'Shipment ID and line number present',        'wms_shipments', N'stg.shipment_line', 'Row', 'Completeness', 'Critical', 'Quarantine', N'shipment_id (max 30 chars) and an integer line_no are required.'),
('SHP-002', N'Ship date is valid and not in the future',   'wms_shipments', N'stg.shipment_line', 'Row', 'Validity',     'Critical', 'Quarantine', N'ship_date must parse, fall inside the date dimension and be on or before extract_date.'),
('SHP-003', N'Shipment line is unique',                    'wms_shipments', N'stg.shipment_line', 'Row', 'Uniqueness',   'High',     'Quarantine', N'Duplicate shipment_id + line_no: the first copy loads, later copies (or copies already in dw) are quarantined.'),
('SHP-004', N'SKU exists in SKU master',                   'wms_shipments', N'stg.shipment_line', 'Row', 'Consistency',  'High',     'Quarantine', N'sku must match ref.sku. An unknown SKU has no crop or case weight, so the line cannot be reported.'),
('SHP-005', N'Cases is a positive whole number',           'wms_shipments', N'stg.shipment_line', 'Row', 'Validity',     'High',     'Quarantine', N'cases must convert to INT and be > 0.'),
('SHP-006', N'Customer is present',                        'wms_shipments', N'stg.shipment_line', 'Row', 'Completeness', 'High',     'Quarantine', N'customer_name is required.'),
('SHP-101', N'One shipments file per day',                 'wms_shipments', N'ops.file_load',     'Set', 'Timeliness',   'High',     'Warn',       N'Every calendar day between the first and last delivered file must have a file. Fails = missing days.'),
('SHP-102', N'Shipment arrived in its own day''s file',    'wms_shipments', N'stg.shipment_line', 'Row', 'Timeliness',   'Low',      'Warn',       N'ship_date earlier than extract_date means the line arrived late (or was re-sent). It still loads.'),
-- cross-system reconciliation ----------------------------------------------------------------------
('REC-001', N'Shipped kg does not exceed sellable harvest',  'cross_system', N'dw.fact_shipment_line', 'Set', 'Consistency', 'High', 'Warn',     N'Per crop, shipped kg must not exceed grade A+B harvested kg by more than 20% (allows for opening stock). Fails = crops over the limit.');
