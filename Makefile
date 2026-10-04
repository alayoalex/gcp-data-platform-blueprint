PY := governance/.venv/Scripts/python.exe
ifeq ($(OS),)
PY := governance/.venv/bin/python
endif

CONTRACTS := products/novamarket-commerce/contracts

.PHONY: help setup test test-all lint gate demo demo-bad compile clean \
        emulator emulator-stop test-integration

help:
	@echo "setup             Crea el entorno virtual e instala Faro en modo editable"
	@echo "test              Tests del motor de gobierno y del enrutamiento de ingesta"
	@echo "lint              Pasa ruff sobre el paquete y los scripts"
	@echo "demo              Corre el gate sobre el producto tal como esta (debe pasar)"
	@echo "demo-bad          Corre el gate sobre un MR con violaciones (debe bloquear)"
	@echo "emulator          Levanta el emulador local de GCP (Floci) en el puerto 4588"
	@echo "test-integration  Tests contra el emulador de GCP (requiere 'make emulator')"
	@echo "compile           Compila el producto Dataform (requiere el CLI de Dataform)"
	@echo "gate              Compila y evalua: lo mismo que corre el CI"

setup:
	python -m venv governance/.venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e "./governance[dev]"
	$(PY) -m pip install google-cloud-storage

# Los tests que no necesitan nada externo. Es lo que corre en cada merge request.
test:
	$(PY) -m pytest governance/tests ingestion/tests/test_routing.py -q

test-all: test test-integration

lint:
	$(PY) -m ruff check .

# --- Emulador local de GCP -------------------------------------------------
#
# Cubre Cloud Storage por completo. NO cubre Dataform, Data Catalog ni Datastream,
# y su BigQuery es solo metadatos: las transformaciones no se verifican aqui.
# Ver docker-compose.yml para el detalle.

emulator:
	docker compose up -d floci-gcp
	@echo "Emulador en http://localhost:4588 — health: /_floci-gcp/health"

emulator-stop:
	docker compose down

test-integration:
	$(PY) -m pytest tests/integration -q

# Las dos demos no necesitan GCP ni Dataform instalados: corren sobre fixtures de
# compilacion versionados en examples/. Es lo que permite mostrar el gate en una
# reunion sin depender de que haya credenciales a mano.
demo:
	@echo "== Producto conforme: el gate debe aprobar =="
	$(PY) -m faro_governance.cli check \
		--source dataform \
		--input examples/dataform-compilation-clean.json \
		--contracts $(CONTRACTS) \
		--format md

demo-bad:
	@echo "== Merge request con violaciones: el gate debe bloquear =="
	-$(PY) -m faro_governance.cli check \
		--source dataform \
		--input examples/dataform-compilation-violations.json \
		--contracts $(CONTRACTS) \
		--format md

compile:
	cd products/novamarket-commerce && dataform compile --json > ../../compilation.json

gate: compile
	$(PY) -m faro_governance.cli check \
		--source dataform \
		--input compilation.json \
		--contracts $(CONTRACTS) \
		--format md \
		--fail-on block

clean:
	rm -rf compilation.json gate-report.md .pytest_cache governance/.pytest_cache
