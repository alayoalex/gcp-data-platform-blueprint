"""Tests de integracion de la ingesta contra el emulador local de GCP (Floci).

Que se prueba aqui y que no, porque importa ser explicito:

**Si**: todo el comportamiento sobre Cloud Storage, que es donde estan los errores que
un test unitario no ve -- que el archivo rechazado se copie a cuarentena, que el motivo
quede escrito al lado, que el original se elimine, y que el evento de escritura que
genera la propia cuarentena no dispare otra ingesta.

**No**: la carga a BigQuery. Floci emula BigQuery a nivel de metadatos, inserts en
streaming y un subconjunto de SQL; no tiene un motor GoogleSQL real ni load jobs desde
GCS. Ese camino se verifica contra un proyecto GCP real, y el test que lo cubre esta
marcado para saltarse con el motivo escrito.

Para correrlos:

    docker compose up -d floci-gcp
    make test-integration

Sin el emulador arriba, se saltan en vez de fallar: un test de integracion que pone rojo
el pipeline de quien no levanto la dependencia ensenia al equipo a ignorar el rojo.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[2] / "ingestion" / "functions" / "pg_export_to_bronze"),
)

EMULATOR_HOST = os.environ.get("STORAGE_EMULATOR_HOST", "http://localhost:4588")
PROJECT_ID = "floci-local"

CONTRATO = {
    "target_table": "raw_supplier_catalog",
    "write_disposition": "WRITE_TRUNCATE",
    "schema": [
        {"name": "supplier_code", "type": "STRING", "mode": "REQUIRED"},
        {"name": "product_sku", "type": "STRING", "mode": "REQUIRED"},
        {"name": "cost_amount", "type": "NUMERIC"},
    ],
}


def _emulator_up() -> bool:
    try:
        with urllib.request.urlopen(f"{EMULATOR_HOST}/_floci-gcp/health", timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


pytestmark = pytest.mark.skipif(
    not _emulator_up(),
    reason=(
        f"El emulador de GCP no responde en {EMULATOR_HOST}. "
        "Levantalo con: make emulator"
    ),
)


@pytest.fixture
def entorno(monkeypatch: pytest.MonkeyPatch) -> str:
    """Configura la funcion contra el emulador y devuelve el nombre del bucket."""
    bucket_name = f"novamarket-landing-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("STORAGE_EMULATOR_HOST", EMULATOR_HOST)
    monkeypatch.setenv("GCP_PROJECT_ID", PROJECT_ID)
    monkeypatch.setenv("SCHEMA_BUCKET", bucket_name)
    monkeypatch.setenv("BRONZE_DATASET", "commerce_bronze")

    import main

    main.settings.cache_clear()  # la configuracion se cachea; el test la invalida
    return bucket_name


@pytest.fixture
def cliente(entorno: str):
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import storage

    client = storage.Client(
        project=PROJECT_ID,
        credentials=AnonymousCredentials(),
        client_options={"api_endpoint": EMULATOR_HOST},
    )
    bucket = client.bucket(entorno)
    bucket.create()
    yield client
    # La limpieza no debe enmascarar el fallo del test que la precede.
    with contextlib.suppress(Exception):
        bucket.delete(force=True)


def _publicar_contrato(cliente, bucket_name: str, feed: str, contrato: dict) -> None:
    cliente.bucket(bucket_name).blob(f"schemas/{feed}.json").upload_from_string(
        json.dumps(contrato), content_type="application/json"
    )


def _subir(cliente, bucket_name: str, ruta: str, contenido: str) -> None:
    cliente.bucket(bucket_name).blob(ruta).upload_from_string(contenido, content_type="text/csv")


# --------------------------------------------------------------------- tests


def test_objeto_fuera_del_patron_se_ignora(cliente, entorno: str) -> None:
    import main

    _subir(cliente, entorno, "otros/README.txt", "hola")
    assert main.handle_event(entorno, "otros/README.txt", storage_client=cliente) == {
        "status": "ignored",
        "object": "otros/README.txt",
    }


def test_feed_sin_contrato_va_a_cuarentena(cliente, entorno: str) -> None:
    """El caso mas comun en produccion: un proveedor empieza a dejar archivos en un
    prefijo nuevo que nadie registro."""
    import main

    objeto = "incoming/feed_desconocido/2026-09-16/datos.csv"
    _subir(cliente, entorno, objeto, "a,b\n1,2\n")

    resultado = main.handle_event(entorno, objeto, storage_client=cliente)

    assert resultado["status"] == "quarantined"
    assert "No existe contrato de ingesta" in resultado["reason"]


def test_la_cuarentena_conserva_el_archivo_y_escribe_el_motivo(cliente, entorno: str) -> None:
    import main

    objeto = "incoming/feed_desconocido/2026-09-16/datos.csv"
    contenido = "a,b\n1,2\n"
    _subir(cliente, entorno, objeto, contenido)

    main.handle_event(entorno, objeto, storage_client=cliente)

    bucket = cliente.bucket(entorno)
    en_cuarentena = [b for b in cliente.list_blobs(bucket, prefix="_quarantine/") if b.name.endswith(".csv")]
    motivos = [b for b in cliente.list_blobs(bucket, prefix="_quarantine/") if b.name.endswith(".reason.txt")]

    assert len(en_cuarentena) == 1, "el archivo rechazado debe conservarse, no descartarse"
    assert en_cuarentena[0].download_as_text() == contenido
    assert len(motivos) == 1, "el motivo debe viajar junto al archivo"

    motivo = motivos[0].download_as_text()
    assert "No existe contrato de ingesta" in motivo
    assert objeto in motivo

    assert not bucket.blob(objeto).exists(), "el original debe eliminarse del prefijo de entrada"


def test_el_archivo_en_cuarentena_no_dispara_otra_ingesta(cliente, entorno: str) -> None:
    """La propiedad que evita el bucle infinito. Escribir en cuarentena genera otro
    evento de finalizacion sobre el mismo bucket; si esa ruta volviera a calzar con el
    patron, la funcion se llamaria a si misma para siempre."""
    import main

    objeto = "incoming/feed_desconocido/2026-09-16/datos.csv"
    _subir(cliente, entorno, objeto, "a,b\n1,2\n")
    main.handle_event(entorno, objeto, storage_client=cliente)

    bucket = cliente.bucket(entorno)
    for blob in cliente.list_blobs(bucket, prefix="_quarantine/"):
        assert main.handle_event(entorno, blob.name, storage_client=cliente)["status"] == "ignored"


def test_contrato_malformado_va_a_cuarentena(cliente, entorno: str) -> None:
    """Un contrato sin `target_table` es un error de configuracion, y el archivo que
    lo gatillo tiene que quedar recuperable igual que cualquier otro rechazo."""
    import main

    _publicar_contrato(cliente, entorno, "supplier_catalog", {"write_disposition": "WRITE_TRUNCATE"})
    objeto = "incoming/supplier_catalog/2026-09-16/catalogo.csv"
    _subir(cliente, entorno, objeto, "supplier_code,product_sku\nACME,SKU-1\n")

    resultado = main.handle_event(entorno, objeto, storage_client=cliente)

    assert resultado["status"] == "quarantined"
    assert "no declara" in resultado["reason"]


def test_el_contrato_se_lee_del_bucket(cliente, entorno: str) -> None:
    """Agregar un feed debe ser un cambio de configuracion, no un redespliegue."""
    import main

    _publicar_contrato(cliente, entorno, "supplier_catalog", CONTRATO)
    config = main.FeedConfig.load(cliente, "supplier_catalog")

    assert config.target_table == "raw_supplier_catalog"
    assert [f["name"] for f in config.schema] == ["supplier_code", "product_sku", "cost_amount"]


@pytest.mark.skip(
    reason=(
        "Floci emula BigQuery a nivel de metadatos, inserts en streaming y un subconjunto "
        "de SQL: no soporta load jobs desde GCS ni particiones con decorador ($YYYYMMDD). "
        "El camino feliz de carga se verifica contra un proyecto GCP real."
    )
)
def test_carga_exitosa_a_bronze(cliente, entorno: str) -> None:
    import main

    _publicar_contrato(cliente, entorno, "supplier_catalog", CONTRATO)
    objeto = "incoming/supplier_catalog/2026-09-16/catalogo.csv"
    _subir(cliente, entorno, objeto, "supplier_code,product_sku,cost_amount\nACME,SKU-1,1500\n")

    resultado = main.handle_event(entorno, objeto, storage_client=cliente)

    assert resultado["status"] == "loaded"
    assert resultado["rows"] == 1
