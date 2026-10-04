from __future__ import annotations

from pathlib import Path

import pytest

from faro_governance.contracts import ContractEngine, DataContract, diff_contracts, load_contracts
from faro_governance.models import Column, DataAsset, Severity

CONTRATO_BASE = """\
apiVersion: faro/v1
kind: DataContract
metadata:
  name: dmt_sales_daily
  product: novamarket-commerce
  version: 1.0.0
  owner: equipo-comercial@novamarket.cl
spec:
  asset: novamarket-data-prod.commerce_gold.dmt_sales_daily
  layer: GOLD
  description: Ventas netas diarias por producto.
  grain: [sale_date, product_id]
  freshness:
    max_delay: PT6H
  retention: P5Y
  consumers:
    - equipo-finanzas@novamarket.cl
  schema:
    - name: sale_date
      type: DATE
      classification: INTERNAL
    - name: product_id
      type: INT64
      classification: INTERNAL
    - name: net_sales_amount
      type: NUMERIC
      classification: INTERNAL
"""


@pytest.fixture
def contrato(tmp_path: Path) -> DataContract:
    p = tmp_path / "dmt_sales_daily.yaml"
    p.write_text(CONTRATO_BASE, encoding="utf-8")
    return DataContract.load(p)


@pytest.fixture
def engine() -> ContractEngine:
    return ContractEngine()


def activo(columns: list[Column], name: str = "dmt_sales_daily", dataset: str = "commerce_gold") -> DataAsset:
    return DataAsset(
        project_id="novamarket-data-prod",
        dataset_id=dataset,
        name=name,
        description="Ventas netas diarias por producto.",
        columns=columns,
    )


def columnas_ok() -> list[Column]:
    return [
        Column("sale_date", "DATE", "Dia de la venta."),
        Column("product_id", "INT64", "Identificador del producto."),
        Column("net_sales_amount", "NUMERIC", "Monto neto vendido."),
    ]


def test_activo_que_cumple_no_genera_hallazgos(engine: ContractEngine, contrato: DataContract) -> None:
    assert engine.evaluate(activo(columnas_ok()), contrato) == []


def test_gold_sin_contrato_bloquea(engine: ContractEngine) -> None:
    (hallazgo,) = engine.evaluate(activo(columnas_ok()), None)
    assert hallazgo.check == "contract.missing"
    assert hallazgo.severity is Severity.BLOCK


def test_silver_sin_contrato_no_bloquea(engine: ContractEngine) -> None:
    a = activo(columnas_ok(), name="mst_customer", dataset="commerce_silver")
    assert engine.evaluate(a, None) == []


def test_columna_prometida_ausente_es_breaking(engine: ContractEngine, contrato: DataContract) -> None:
    a = activo([c for c in columnas_ok() if c.name != "net_sales_amount"])
    hallazgo = next(h for h in engine.evaluate(a, contrato) if h.check == "contract.breaking_change")
    assert hallazgo.severity is Severity.BLOCK
    assert "equipo-finanzas@novamarket.cl" in (hallazgo.recommendation or "")


def test_columna_extra_solo_advierte(engine: ContractEngine, contrato: DataContract) -> None:
    a = activo([*columnas_ok(), Column("gross_sales_amount", "NUMERIC", "Bruto.")])
    hallazgo = next(h for h in engine.evaluate(a, contrato) if h.check == "contract.undeclared_column")
    assert hallazgo.severity is Severity.WARN


def test_cambio_de_tipo_incompatible_bloquea(engine: ContractEngine, contrato: DataContract) -> None:
    cols = columnas_ok()
    cols[2] = Column("net_sales_amount", "STRING", "Monto como texto.")
    hallazgos = engine.evaluate(activo(cols), contrato)
    hallazgo = next(h for h in hallazgos if h.check == "contract.breaking_change")
    assert hallazgo.severity is Severity.BLOCK


def test_ampliacion_de_tipo_solo_desactualiza_el_contrato(
    engine: ContractEngine, contrato: DataContract
) -> None:
    """INT64 -> NUMERIC no rompe a nadie: el valor sigue cabiendo."""
    cols = columnas_ok()
    cols[1] = Column("product_id", "NUMERIC", "Identificador del producto.")
    hallazgo = next(h for h in engine.evaluate(activo(cols), contrato) if h.check == "contract.type_drift")
    assert hallazgo.severity is Severity.WARN


def test_grano_invalido_bloquea(engine: ContractEngine, tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        CONTRATO_BASE.replace("grain: [sale_date, product_id]", "grain: [sale_day]"),
        encoding="utf-8",
    )
    hallazgos = engine.evaluate(activo(columnas_ok()), DataContract.load(p))
    assert any(h.check == "contract.invalid_grain" and h.severity is Severity.BLOCK for h in hallazgos)


def test_contrato_sin_duenio_bloquea(engine: ContractEngine, tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(CONTRATO_BASE.replace("  owner: equipo-comercial@novamarket.cl\n", ""), encoding="utf-8")
    hallazgo = next(
        h
        for h in engine.evaluate(activo(columnas_ok()), DataContract.load(p))
        if h.check == "contract.incomplete"
    )
    assert "metadata.owner" in hallazgo.message


def test_subclasificacion_bloquea(engine: ContractEngine, tmp_path: Path) -> None:
    """El contrato dice INTERNAL, el clasificador detecta un correo. Gana el clasificador."""
    contrato_malo = CONTRATO_BASE + (
        "    - name: customer_email\n"
        "      type: STRING\n"
        "      classification: INTERNAL\n"
    )
    p = tmp_path / "c.yaml"
    p.write_text(contrato_malo, encoding="utf-8")
    a = activo([*columnas_ok(), Column("customer_email", "STRING", "Correo del cliente.")])
    hallazgo = next(
        h for h in engine.evaluate(a, DataContract.load(p)) if h.check == "contract.under_classified"
    )
    assert hallazgo.severity is Severity.BLOCK
    assert "person.contact" in (hallazgo.recommendation or "")


def test_carga_de_directorio_indexa_por_activo(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(CONTRATO_BASE, encoding="utf-8")
    contratos = load_contracts(tmp_path)
    assert "novamarket-data-prod.commerce_gold.dmt_sales_daily" in contratos


# --------------------------------------------------------------------- diff


def _contract(tmp_path: Path, body: str, name: str) -> DataContract:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return DataContract.load(p)


def test_diff_detecta_columna_eliminada(tmp_path: Path) -> None:
    nuevo = CONTRATO_BASE.replace(
        "    - name: net_sales_amount\n      type: NUMERIC\n      classification: INTERNAL\n", ""
    )
    cambios = diff_contracts(
        _contract(tmp_path, CONTRATO_BASE, "old.yaml"), _contract(tmp_path, nuevo, "new.yaml")
    )
    assert cambios == ["columna eliminada: net_sales_amount"]


def test_diff_ignora_ampliacion_compatible(tmp_path: Path) -> None:
    nuevo = CONTRATO_BASE.replace(
        "    - name: product_id" + chr(10) + "      type: INT64",
        "    - name: product_id" + chr(10) + "      type: NUMERIC",
    )
    cambios = diff_contracts(
        _contract(tmp_path, CONTRATO_BASE, "old.yaml"), _contract(tmp_path, nuevo, "new.yaml")
    )
    assert cambios == []


def test_diff_detecta_cambio_de_grano(tmp_path: Path) -> None:
    nuevo = CONTRATO_BASE.replace("grain: [sale_date, product_id]", "grain: [sale_date]")
    cambios = diff_contracts(
        _contract(tmp_path, CONTRATO_BASE, "old.yaml"), _contract(tmp_path, nuevo, "new.yaml")
    )
    assert any("cambio de grano" in c for c in cambios)
