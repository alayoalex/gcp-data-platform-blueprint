"""Tests de la logica de enrutamiento de la ingesta. No necesitan nube ni emulador.

Es la logica que mas se equivoca en una funcion de ingesta y la unica que se puede
probar sin infraestructura. Los tests que si necesitan el emulador estan en
`test_gcs_integration.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "functions" / "pg_export_to_bronze"))

from main import OBJECT_PATTERN, quarantine_path, route


@pytest.mark.parametrize(
    "objeto, feed, particion",
    [
        ("incoming/supplier_catalog/2026-09-16/catalogo.csv", "supplier_catalog", "2026-09-16"),
        ("incoming/marketing_export/2026-01-01/export-01.csv", "marketing_export", "2026-01-01"),
    ],
)
def test_objeto_valido_se_enruta(objeto: str, feed: str, particion: str) -> None:
    assert route(objeto) == (feed, particion)


@pytest.mark.parametrize(
    "objeto",
    [
        "_quarantine/20260916T120000Z/incoming/x/2026-09-16/a.csv",  # ya en cuarentena
        "schemas/supplier_catalog.json",  # configuracion, no dato
        "incoming/supplier_catalog/2026-09-16/catalogo.parquet",  # formato no soportado
        "incoming/supplier_catalog/16-09-2026/catalogo.csv",  # fecha en otro formato
        "incoming/Supplier_Catalog/2026-09-16/catalogo.csv",  # feed con mayusculas
        "incoming/supplier_catalog/2026-09-16/sub/carpeta.csv",  # anidado de mas
        "catalogo.csv",  # en la raiz
        "",
    ],
)
def test_objeto_no_valido_se_ignora(objeto: str) -> None:
    """Ignorar y no fallar es deliberado: el mismo bucket recibe la cuarentena y los
    esquemas, y fallar sobre ellos produciria un bucle de reintentos infinito."""
    assert route(objeto) is None


def test_la_cuarentena_conserva_la_ruta_original() -> None:
    """Conservar la ruta completa bajo el prefijo permite reprocesar el archivo
    copiandolo de vuelta sin tener que reconstruir a que feed pertenecia."""
    ruta = quarantine_path("_quarantine", "incoming/supplier_catalog/2026-09-16/a.csv", "20260916T120000Z")
    assert ruta == "_quarantine/20260916T120000Z/incoming/supplier_catalog/2026-09-16/a.csv"


def test_un_archivo_en_cuarentena_no_se_reingesta() -> None:
    """La propiedad que evita el bucle: la ruta de cuarentena nunca vuelve a calzar
    con el patron de ingesta, asi que el evento de escritura que genera se ignora."""
    original = "incoming/supplier_catalog/2026-09-16/a.csv"
    assert route(original) is not None
    assert route(quarantine_path("_quarantine", original, "20260916T120000Z")) is None


def test_el_patron_esta_anclado() -> None:
    """Sin el ancla inicial, `x/incoming/...` tambien calzaria."""
    assert OBJECT_PATTERN.pattern.startswith("^")
    assert route("otra/cosa/incoming/feed/2026-09-16/a.csv") is None
