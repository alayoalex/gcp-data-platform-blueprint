"""Los casos de prueba son, a la vez, la documentacion ejecutable de la convencion.

Cada test que falla aqui es una regla de negocio que cambio, no un detalle tecnico.
"""

from __future__ import annotations

import pytest

from faro_governance.models import AssetType, Column, DataAsset, Layer
from faro_governance.naming import NamingEngine


@pytest.fixture
def engine() -> NamingEngine:
    return NamingEngine()


def asset(name: str, dataset: str = "commerce_gold", **kw) -> DataAsset:
    return DataAsset(
        project_id="novamarket-data-prod",
        dataset_id=dataset,
        name=name,
        description=kw.pop("description", "Tabla de ejemplo con una descripcion suficientemente larga."),
        **kw,
    )


# --------------------------------------------------------------- snake_case


@pytest.mark.parametrize("name", ["dmt_sales_daily", "mst_customer", "raw_shop_order_item"])
def test_snake_case_valido(engine: NamingEngine, name: str) -> None:
    score, _ = engine._snake_case(name)
    assert score == 1.0


@pytest.mark.parametrize(
    "name, techo",
    [("DmtSalesDaily", 0.5), ("dmt sales daily", 0.5), ("dmt-sales-daily", 0.5), ("2024_sales", 0.7)],
)
def test_snake_case_invalido(engine: NamingEngine, name: str, techo: float) -> None:
    score, _ = engine._snake_case(name)
    assert score <= techo


# -------------------------------------------------------------- full_words


def test_abreviatura_penaliza_y_se_explica(engine: NamingEngine) -> None:
    score, reason = engine._full_words("order_qty")
    assert score == 0.5
    assert "quantity" in reason


def test_sigla_autorizada_no_penaliza(engine: NamingEngine) -> None:
    score, _ = engine._full_words("customer_id")
    assert score == 1.0


def test_token_corto_desconocido_penaliza(engine: NamingEngine) -> None:
    score, _ = engine._full_words("ab_customer")
    assert score < 1.0


# --------------------------------------------------------- layer_structure


def test_capa_gold_exige_prefijo(engine: NamingEngine) -> None:
    bueno = engine._layer_structure(asset("dmt_sales_daily", "commerce_gold"))
    malo = engine._layer_structure(asset("sales_daily", "commerce_gold"))
    assert bueno[0] == 1.0
    assert malo[0] == 0.0
    assert "dmt" in malo[1]


def test_capa_se_infiere_del_dataset(engine: NamingEngine) -> None:
    assert asset("x", "commerce_silver").layer is Layer.SILVER
    assert asset("x", "commerce_bronze").layer is Layer.BRONZE


def test_label_explicita_gana_sobre_el_nombre_del_dataset() -> None:
    a = asset("x", "commerce_silver", labels={"layer": "GOLD"})
    assert a.layer is Layer.GOLD


def test_capa_desconocida_no_es_cero(engine: NamingEngine) -> None:
    """Sin capa inferible se penaliza parcialmente: es una senal, no una infraccion."""
    score, _ = engine._layer_structure(asset("cualquier_cosa", "sandbox"))
    assert 0.0 < score < 1.0


# ---------------------------------------------------------- data_category


@pytest.mark.parametrize(
    "col, esperado",
    [
        (Column("sale_date", "DATE"), 1.0),
        (Column("sale_day", "DATE"), 0.0),
        (Column("created_datetime", "TIMESTAMP"), 1.0),
        (Column("is_returned_flag", "BOOL"), 1.0),
        (Column("returned", "BOOL"), 0.0),
        (Column("total_amount", "NUMERIC"), 1.0),
        (Column("total", "NUMERIC"), 0.0),
        (Column("order_id", "INT64"), 1.0),
        (Column("order_hour", "INT64"), 1.0),  # sin pista semantica: no se exige sufijo
        (Column("conversion_rate", "FLOAT64"), 1.0),
    ],
)
def test_categoria_semantica(engine: NamingEngine, col: Column, esperado: float) -> None:
    score, _ = engine._data_category(col)
    assert score == esperado


def test_columna_de_sistema_exenta(engine: NamingEngine) -> None:
    score, _ = engine._data_category(Column("ingestion_datetime", "TIMESTAMP"))
    assert score == 1.0


def test_email_no_se_castiga_por_falta_de_sufijo(engine: NamingEngine) -> None:
    """Falso positivo clasico: `email` es un nombre correcto y no lleva sufijo."""
    score, _ = engine._data_category(Column("email", "STRING"))
    assert score == 1.0


# ------------------------------------------------------------ documented


def test_sin_descripcion_es_cero(engine: NamingEngine) -> None:
    score, _ = engine._documented(None, "dmt_sales_daily")
    assert score == 0.0


def test_descripcion_que_repite_el_nombre_casi_no_vale(engine: NamingEngine) -> None:
    score, _ = engine._documented("dmt sales daily", "dmt_sales_daily")
    assert score <= 0.2


# ------------------------------------------------------------- sugerencias


@pytest.mark.parametrize(
    "entrada, tipo, esperado",
    [
        ("order_qty", "INT64", "order_quantity"),
        ("order_dow", "INT64", "order_day_of_week"),
        ("createdAt", "TIMESTAMP", "created_at"),
        ("total", "NUMERIC", "total_amount"),
        ("returned", "BOOL", "returned_flag"),
    ],
)
def test_sugerencia_de_columna(engine: NamingEngine, entrada: str, tipo: str, esperado: str) -> None:
    assert engine.suggest_name(entrada, column=Column(entrada, tipo)) == esperado


def test_sugerencia_de_tabla_agrega_prefijo_de_capa(engine: NamingEngine) -> None:
    assert engine.suggest_name("sales_daily", layer=Layer.GOLD) == "dmt_sales_daily"


def test_sugerencia_no_duplica_prefijo_existente(engine: NamingEngine) -> None:
    assert engine.suggest_name("dmt_sales_daily", layer=Layer.GOLD) == "dmt_sales_daily"


# ------------------------------------------------------------- integracion


def test_tabla_conforme_aprueba(engine: NamingEngine) -> None:
    a = asset(
        "dmt_sales_daily",
        "commerce_gold",
        description="Ventas netas por dia y producto, con impuestos excluidos.",
        columns=[
            Column("sale_date", "DATE", "Dia calendario de la venta en zona horaria America/Santiago."),
            Column("net_sales_amount", "NUMERIC", "Monto neto vendido en pesos chilenos."),
        ],
    )
    resultados = engine.evaluate(a)
    assert len(resultados) == 3
    assert all(r.passed for r in resultados), [r.message for r in resultados if not r.passed]


def test_tabla_no_conforme_reprueba_y_recomienda(engine: NamingEngine) -> None:
    a = asset(
        "SalesDaily",
        "commerce_gold",
        description=None,
        columns=[Column("qty", "INT64", None)],
    )
    tabla, columna = engine.evaluate(a)
    assert not tabla.passed
    assert tabla.recommendation == "dmt_sales_daily"
    assert not columna.passed
    assert columna.recommendation == "quantity"


def test_dataset_se_evalua_con_su_propia_politica(engine: NamingEngine) -> None:
    ds = DataAsset(
        project_id="novamarket-data-prod",
        dataset_id="commerce_gold",
        name="commerce_gold",
        asset_type=AssetType.DATASET,
        description="Productos de datos de comercio listos para consumo analitico.",
    )
    (resultado,) = engine.evaluate(ds)
    assert resultado.check == "naming.dataset"
    assert resultado.passed


# ------------------------------------------------- ventana temporal en features


@pytest.mark.parametrize(
    "columna",
    ["net_amount_90d", "order_quantity_90d", "session_quantity_30d", "reorder_percentage_90d"],
)
def test_sufijo_de_ventana_no_invalida_la_categoria(engine: NamingEngine, columna: str) -> None:
    """Una tabla de features nombra ventanas: `net_amount_90d` es correcto.
    Sin esta regla, toda la capa feature seria no conforme."""
    score, _ = engine._data_category(Column(columna, "NUMERIC"))
    assert score == 1.0


def test_la_sugerencia_conserva_la_ventana(engine: NamingEngine) -> None:
    assert engine.suggest_name("net_amt_90d", column=Column("net_amt_90d", "NUMERIC")) == "net_amount_90d"


def test_created_at_es_conforme(engine: NamingEngine) -> None:
    """`_at` es el sufijo de marca de tiempo sancionado; la regla de palabras
    completas no debe castigar la convencion que la de categoria exige."""
    col = Column("created_at", "TIMESTAMP", "Momento de creacion del registro.")
    categoria, _ = engine._data_category(col)
    palabras, _ = engine._full_words(col.name)
    assert categoria == 1.0
    assert palabras == 1.0
