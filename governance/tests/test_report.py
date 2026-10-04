from __future__ import annotations

import json
from pathlib import Path

import pytest

from faro_governance.models import Assessment, Report, Severity
from faro_governance.report import to_codequality, to_markdown, to_text

WINDOWS_PATH = "definitions" + chr(92) + "gold" + chr(92) + "asset.sqlx"


def _report(**kw) -> Report:
    base = dict(
        asset_id="bigquery://proj.commerce_gold.dmt_sales_daily",
        target="dmt_sales_daily.total",
        check="naming.column",
        score=0.4,
        threshold=0.8,
        severity=Severity.BLOCK,
        message="Criterios no satisfechos: data_category.",
        recommendation="total_amount",
        source_path="definitions/gold/dmt_sales_daily.sqlx",
    )
    base.update(kw)
    return Report(assessments=[Assessment(**base)], assets_evaluated=1)


def test_codequality_es_json_valido() -> None:
    items = json.loads(to_codequality(_report()))
    assert len(items) == 1
    assert set(items[0]) == {"description", "check_name", "fingerprint", "severity", "location"}


@pytest.mark.parametrize(
    "severidad, esperado",
    [(Severity.BLOCK, "blocker"), (Severity.WARN, "major"), (Severity.INFO, "info")],
)
def test_mapeo_de_severidad(severidad: Severity, esperado: str) -> None:
    items = json.loads(to_codequality(_report(severity=severidad)))
    assert items[0]["severity"] == esperado


def test_el_fingerprint_es_estable_entre_corridas() -> None:
    """GitLab lo usa para distinguir hallazgos nuevos de preexistentes. Si cambiara,
    cada push mostraria todo como nuevo y el widget dejaria de significar algo."""
    uno = json.loads(to_codequality(_report()))[0]["fingerprint"]
    dos = json.loads(to_codequality(_report()))[0]["fingerprint"]
    assert uno == dos


def test_el_fingerprint_no_depende_del_mensaje() -> None:
    """Reformular un mensaje de la politica no debe reabrir hallazgos ya conocidos."""
    uno = json.loads(to_codequality(_report()))[0]["fingerprint"]
    dos = json.loads(to_codequality(_report(message="Otro texto.")))[0]["fingerprint"]
    assert uno == dos


def test_el_fingerprint_distingue_hallazgos_distintos() -> None:
    uno = json.loads(to_codequality(_report()))[0]["fingerprint"]
    dos = json.loads(to_codequality(_report(check="privacy.treatment")))[0]["fingerprint"]
    assert uno != dos


def test_prefijo_de_ruta_para_proyecto_anidado() -> None:
    items = json.loads(to_codequality(_report(), path_prefix="products/novamarket-commerce"))
    assert items[0]["location"]["path"] == (
        "products/novamarket-commerce/definitions/gold/dmt_sales_daily.sqlx"
    )


def test_no_duplica_el_prefijo_en_rutas_ya_relativas_a_la_raiz(tmp_path: Path) -> None:
    """Los hallazgos de contrato traen la ruta relativa a la raiz; anteponerle el
    prefijo produciria products/x/products/x/... y GitLab no anclaria nada."""
    real = tmp_path / "contracts" / "dmt_sales_daily.yaml"
    real.parent.mkdir(parents=True)
    real.write_text("apiVersion: faro/v1\n", encoding="utf-8")

    items = json.loads(
        to_codequality(
            _report(source_path="contracts/dmt_sales_daily.yaml"),
            path_prefix="products/novamarket-commerce",
            repo_root=tmp_path,
        )
    )
    assert items[0]["location"]["path"] == "contracts/dmt_sales_daily.yaml"


def test_normaliza_separadores_de_windows() -> None:
    items = json.loads(to_codequality(_report(source_path=WINDOWS_PATH)))
    assert chr(92) not in items[0]["location"]["path"]
    assert items[0]["location"]["path"] == "definitions/gold/asset.sqlx"


def test_ancla_la_linea_donde_aparece_el_objetivo(tmp_path: Path) -> None:
    f = tmp_path / "definitions" / "gold" / "dmt_sales_daily.sqlx"
    f.parent.mkdir(parents=True)
    f.write_text('config {\n  type: "table"\n}\n\nSELECT\n  total\nFROM x\n', encoding="utf-8")
    items = json.loads(to_codequality(_report(), repo_root=tmp_path))
    assert items[0]["location"]["lines"]["begin"] == 6


def test_archivo_inexistente_no_rompe_el_reporte() -> None:
    items = json.loads(to_codequality(_report(source_path="no/existe.sqlx")))
    assert items[0]["location"]["lines"]["begin"] == 1


def test_reporte_sin_hallazgos_es_lista_vacia() -> None:
    assert json.loads(to_codequality(Report(assets_evaluated=3))) == []


def test_markdown_sin_hallazgos_lo_dice() -> None:
    assert "sin hallazgos" in to_markdown(Report(assets_evaluated=3)).lower()


def test_texto_incluye_la_recomendacion() -> None:
    assert "total_amount" in to_text(_report())
