"""Verificacion del escaneo de descubrimiento contra BigQuery emulado.

Esto es lo que corre en la fase 0 frente a un cliente: se apunta al warehouse existente
y se obtiene el diagnostico. Que funcione de verdad contra una API de BigQuery -- aunque
sea emulada -- vale mas que cualquier test con dobles, porque el adaptador se equivoca
justamente en los detalles de la API: como se leen las labels, de donde sale el policy
tag de una columna, que devuelve `table_type` para una vista.

Limitacion conocida del emulador: no conserva la configuracion de particionado
(`time_partitioning` vuelve como `None`). No afecta a estos tests, porque el gate no
evalua particionado, pero conviene saberlo antes de intentar verificar aqui algo que
dependa de ello.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request

import pytest

EMULATOR_HOST = os.environ.get("BIGQUERY_EMULATOR_HOST", "http://localhost:4588")
PROJECT_ID = "floci-scan-test"


def _emulator_up() -> bool:
    try:
        with urllib.request.urlopen(f"{EMULATOR_HOST}/_floci-gcp/health", timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


pytestmark = pytest.mark.skipif(
    not _emulator_up(),
    reason=f"El emulador de GCP no responde en {EMULATOR_HOST}. Levantalo con: make emulator",
)


@pytest.fixture(scope="module")
def client():
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    return bigquery.Client(
        project=PROJECT_ID,
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=EMULATOR_HOST),
    )


@pytest.fixture(scope="module")
def warehouse(client):
    """Monta un warehouse pequenio que imita lo que se encuentra en un cliente real:
    una capa gold bien hecha y una tabla heredada con todos los problemas juntos."""
    from google.cloud import bigquery

    gold = bigquery.Dataset(f"{PROJECT_ID}.commerce_gold")
    gold.location = "us-central1"
    gold.labels = {"layer": "gold", "product": "novamarket-commerce"}
    client.create_dataset(gold, exists_ok=True)

    conforme = bigquery.Table(
        f"{PROJECT_ID}.commerce_gold.dmt_sales_daily",
        schema=[
            bigquery.SchemaField(
                "sale_date", "DATE", description="Dia calendario de la venta en America/Santiago."
            ),
            bigquery.SchemaField(
                "product_key", "INT64", description="Identificador del producto vendido."
            ),
            bigquery.SchemaField(
                "net_sales_amount", "NUMERIC", description="Monto neto vendido, sin canceladas."
            ),
        ],
    )
    conforme.description = "Ventas netas diarias por producto. Excluye ordenes canceladas."
    client.create_table(conforme, exists_ok=True)

    heredada = bigquery.Table(
        f"{PROJECT_ID}.commerce_gold.ClientesLegacy",
        schema=[
            bigquery.SchemaField("usr_id", "INT64"),
            bigquery.SchemaField("email", "STRING", description="Correo"),
            bigquery.SchemaField("total_amt", "NUMERIC"),
        ],
    )
    client.create_table(heredada, exists_ok=True)

    yield PROJECT_ID

    for name in ("dmt_sales_daily", "ClientesLegacy"):
        client.delete_table(f"{PROJECT_ID}.commerce_gold.{name}", not_found_ok=True)
    client.delete_dataset(f"{PROJECT_ID}.commerce_gold", delete_contents=True, not_found_ok=True)


# --------------------------------------------------------------------- tests


def test_el_adaptador_lee_los_activos_del_proyecto(client, warehouse) -> None:
    from faro_governance.parser import parse_bigquery_api

    activos = {a.name: a for a in parse_bigquery_api(client, warehouse, ["commerce_gold"])}

    assert {"dmt_sales_daily", "ClientesLegacy"} <= set(activos)
    assert activos["dmt_sales_daily"].description.startswith("Ventas netas diarias")
    assert [c.name for c in activos["dmt_sales_daily"].columns] == [
        "sale_date",
        "product_key",
        "net_sales_amount",
    ]


def test_los_tipos_llegan_reales(client, warehouse) -> None:
    """La diferencia con la compilacion de Dataform: ahi los tipos son UNKNOWN y las
    reglas que dependen del tipo se abstienen. Aqui se evaluan de verdad."""
    from faro_governance.parser import UNKNOWN_TYPE, parse_bigquery_api

    activos = {a.name: a for a in parse_bigquery_api(client, warehouse, ["commerce_gold"])}
    tipos = {c.name: c.data_type for c in activos["dmt_sales_daily"].columns}

    assert tipos == {"sale_date": "DATE", "product_key": "INTEGER", "net_sales_amount": "NUMERIC"}
    assert UNKNOWN_TYPE not in tipos.values()


def test_la_capa_se_infiere_de_la_label_del_dataset(client, warehouse) -> None:
    """Es la ventaja concreta de leer por API en vez de exportar INFORMATION_SCHEMA:
    las labels vienen incluidas, asi que la capa es una declaracion y no una heuristica
    sobre el nombre del dataset."""
    from faro_governance.models import Layer
    from faro_governance.parser import parse_bigquery_api

    activos = parse_bigquery_api(client, warehouse, ["commerce_gold"])
    assert all(a.layer is Layer.GOLD for a in activos)
    assert all(a.labels.get("product") == "novamarket-commerce" for a in activos)


def test_el_gate_aprueba_el_activo_conforme(client, warehouse) -> None:
    from faro_governance.naming import NamingEngine
    from faro_governance.parser import parse_bigquery_api

    activos = {a.name: a for a in parse_bigquery_api(client, warehouse, ["commerce_gold"])}
    resultados = NamingEngine().evaluate(activos["dmt_sales_daily"])

    fallos = [r for r in resultados if not r.passed]
    assert not fallos, [f"{r.target}: {r.message}" for r in fallos]


def test_el_gate_detecta_la_tabla_heredada(client, warehouse) -> None:
    """El hallazgo tipico del diagnostico: nombre en camelCase, sin descripcion,
    abreviaturas y un correo sin etiquetar en una capa de consumo."""
    from faro_governance.naming import NamingEngine
    from faro_governance.parser import parse_bigquery_api
    from faro_governance.privacy import PrivacyEngine

    activos = {a.name: a for a in parse_bigquery_api(client, warehouse, ["commerce_gold"])}
    legacy = activos["ClientesLegacy"]

    naming = {r.target: r for r in NamingEngine().evaluate(legacy) if not r.passed}
    assert "ClientesLegacy" in naming
    assert naming["ClientesLegacy"].recommendation == "dmt_clientes_legacy"
    assert naming["ClientesLegacy.usr_id"].recommendation == "user_id"
    assert naming["ClientesLegacy.total_amt"].recommendation == "total_amount"

    privacidad = {h.check for h in PrivacyEngine().evaluate(legacy)}
    assert "privacy.missing_policy_tag" in privacidad
    assert "privacy.treatment" in privacidad


def test_el_escaneo_completo_produce_un_reporte(client, warehouse) -> None:
    """Lo que se le entrega al cliente en la fase 0."""
    from faro_governance.cli import run_checks
    from faro_governance.parser import parse_bigquery_api
    from faro_governance.report import to_markdown

    activos = parse_bigquery_api(client, warehouse, ["commerce_gold"])
    reporte = run_checks(activos, None, checks={"naming", "privacy"})

    assert reporte.assets_evaluated == len(activos)
    assert reporte.failures, "un warehouse con una tabla heredada debe producir hallazgos"

    markdown = to_markdown(reporte)
    assert "ClientesLegacy" in markdown
    assert "dmt_sales_daily" not in markdown, "el activo conforme no debe aparecer en el reporte"
