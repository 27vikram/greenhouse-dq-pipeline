# Shortcuts. Everything runs in Docker; the host only needs Docker Desktop.
RUN = docker compose run --rm app python

.PHONY: up build generate setup load all sql down reset

up:            ## start SQL Server and wait until it is healthy
	docker compose up -d --wait sqlserver

build:         ## build the Python image (first run, or after changing requirements.txt)
	docker compose build app

generate:      ## create the synthetic source files in data/
	$(RUN) generator/generate_data.py

setup: up      ## (re)build the database: drops and recreates all tables
	$(RUN) pipeline/setup_db.py

load: up       ## land source files into staging
	$(RUN) pipeline/load_staging.py

all: generate setup load

sql: up        ## open a SQL prompt on GreenhouseDW (type a query, then GO; type exit to leave)
	docker exec -it greenhouse-sql bash -c '/opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$$MSSQL_SA_PASSWORD" -C -d GreenhouseDW'

down:          ## stop SQL Server (data is kept)
	docker compose stop

reset:         ## delete the container AND its data
	docker compose down -v
