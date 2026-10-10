/* 01_schemas.sql — one schema per layer of the pipeline
   ref  master data and configuration (zones, crops, SKUs, valid sensor ranges)
   stg  raw landing area: every column is text, nothing is rejected here
   dw   typed star schema that reports read from
   dq   data quality rule catalogue and results
   err  rows quarantined by a DQ rule, kept as JSON for triage
   ops  pipeline run and file audit log
*/
SET NOCOUNT ON;
IF SCHEMA_ID(N'ref') IS NULL EXEC(N'CREATE SCHEMA ref');
IF SCHEMA_ID(N'stg') IS NULL EXEC(N'CREATE SCHEMA stg');
IF SCHEMA_ID(N'dw')  IS NULL EXEC(N'CREATE SCHEMA dw');
IF SCHEMA_ID(N'dq')  IS NULL EXEC(N'CREATE SCHEMA dq');
IF SCHEMA_ID(N'err') IS NULL EXEC(N'CREATE SCHEMA err');
IF SCHEMA_ID(N'ops') IS NULL EXEC(N'CREATE SCHEMA ops');
