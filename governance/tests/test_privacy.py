from __future__ import annotations

import pytest

from faro_governance.models import Column, DataAsset, Sensitivity, Severity
from faro_governance.privacy import Classification, PrivacyClassifier, PrivacyEngine


@pytest.fixture
def classifier() -> PrivacyClassifier:
    return PrivacyClassifier()


@pytest.fixture
def engine() -> PrivacyEngine:
    return PrivacyEngine()


def asset(dataset: str, name: str, columns: list[Column]) -> DataAsset:
    return DataAsset(
        project_id="novamarket-data-prod",
        dataset_id=dataset,
        name=name,
        description="Activo de prueba con descripcion suficiente.",
        columns=columns,
    )


# ------------------------------------------------------------ clasificacion


@pytest.mark.parametrize(
    "columna, categoria, sensibilidad",
    [
        ("email", "contact", Sensitivity.CONFIDENTIAL),
        ("phone", "contact", Sensitivity.CONFIDENTIAL),
        ("full_name", "person_name", Sensitivity.CONFIDENTIAL),
        ("address_line1", "location", Sensitivity.CONFIDENTIAL),
        ("zip_code", "location", Sensitivity.CONFIDENTIAL),
        ("hashed_password", "credential", Sensitivity.RESTRICTED),
        ("customer_rut", "national_id", Sensitivity.RESTRICTED),
        ("card_number", "payment_instrument", Sensitivity.RESTRICTED),
        ("ip_address", "online_identifier", Sensitivity.CONFIDENTIAL),
        ("session_id", "online_identifier", Sensitivity.CONFIDENTIAL),
        ("birth_date", "demographic", Sensitivity.CONFIDENTIAL),
        ("user_id", "person_reference", Sensitivity.CONFIDENTIAL),
    ],
)
def test_deteccion_por_catalogo(
    classifier: PrivacyClassifier, columna: str, categoria: str, sensibilidad: Sensitivity
) -> None:
    a = asset("commerce_silver", "mst_customer", [Column(columna, "STRING")])
    resultado = classifier.classify_column(a, a.columns[0])
    assert resultado.category == categoria
    assert resultado.sensitivity is sensibilidad


@pytest.mark.parametrize(
    "columna",
    ["order_quantity", "net_sales_amount", "product_name", "department_id", "sale_date"],
)
def test_no_personal_no_se_marca(classifier: PrivacyClassifier, columna: str) -> None:
    a = asset("commerce_gold", "dmt_sales_daily", [Column(columna, "STRING")])
    assert classifier.classify_column(a, a.columns[0]).category is None


@pytest.mark.parametrize("columna", ["has_email_flag", "email_count", "customer_id_hash_code"])
def test_exclusiones_evitan_falsos_positivos(classifier: PrivacyClassifier, columna: str) -> None:
    """Un contador de correos no es un correo. Estos son los falsos positivos que
    hacen que un equipo deje de creerle al gate."""
    a = asset("commerce_gold", "dmt_customer_activity", [Column(columna, "INT64")])
    assert classifier.classify_column(a, a.columns[0]).category is None


def test_contexto_sube_la_confianza(classifier: PrivacyClassifier) -> None:
    en_contexto = asset("customer_silver", "mst_customer", [Column("full_name", "STRING")])
    fuera = asset("catalog_silver", "mst_product", [Column("full_name", "STRING")])
    alta = classifier.classify_column(en_contexto, en_contexto.columns[0])
    baja = classifier.classify_column(fuera, fuera.columns[0])
    assert alta.confidence > baja.confidence


def test_identificador_fuerte_no_depende_del_contexto(classifier: PrivacyClassifier) -> None:
    fuera = asset("catalog_silver", "mst_product", [Column("customer_rut", "STRING")])
    assert classifier.classify_column(fuera, fuera.columns[0]).confidence >= 0.90


def test_el_orden_del_catalogo_resuelve_ambiguedad(classifier: PrivacyClassifier) -> None:
    """`hashed_password` calza credential antes que cualquier regla de nombre."""
    a = asset("commerce_bronze", "raw_shop_user", [Column("hashed_password", "STRING")])
    assert classifier.classify_column(a, a.columns[0]).category == "credential"


# -------------------------------------------------------------- fiscalizar


def test_credencial_se_bloquea_en_toda_capa(engine: PrivacyEngine) -> None:
    a = asset("commerce_bronze", "raw_shop_user", [Column("hashed_password", "STRING")])
    hallazgos = engine.evaluate(a)
    prohibido = [h for h in hallazgos if h.check == "privacy.forbidden"]
    assert prohibido and prohibido[0].severity is Severity.BLOCK


def test_pii_en_gold_se_bloquea(engine: PrivacyEngine) -> None:
    a = asset("commerce_gold", "dmt_customer_daily", [Column("email", "STRING")])
    checks = {h.check for h in engine.evaluate(a)}
    assert "privacy.treatment" in checks
    assert "privacy.missing_policy_tag" in checks


def test_pii_en_silver_sin_policy_tag_advierte(engine: PrivacyEngine) -> None:
    a = asset("commerce_silver", "mst_customer", [Column("email", "STRING")])
    tag = next(h for h in engine.evaluate(a) if h.check == "privacy.missing_policy_tag")
    assert tag.severity is Severity.WARN
    assert "person.contact" in (tag.recommendation or "")


def test_pii_con_policy_tag_y_hash_pasa_en_silver(engine: PrivacyEngine) -> None:
    a = asset(
        "commerce_silver",
        "mst_customer",
        [Column("customer_id_hash", "STRING", policy_tag="person.reference")],
    )
    bloqueos = [h for h in engine.evaluate(a) if h.severity is Severity.BLOCK]
    assert not bloqueos, [h.message for h in bloqueos]


def test_bronze_permite_pii_en_claro(engine: PrivacyEngine) -> None:
    """Bronze es la copia fiel del origen: el control ahi es de acceso, no de forma."""
    a = asset("commerce_bronze", "raw_shop_user", [Column("email", "STRING")])
    assert not [h for h in engine.evaluate(a) if h.severity is Severity.BLOCK]


def test_dato_sensible_siempre_deja_rastro(engine: PrivacyEngine) -> None:
    a = asset("health_silver", "mst_patient", [Column("medical_condition", "STRING")])
    especiales = [h for h in engine.evaluate(a) if h.check == "privacy.special_category"]
    assert especiales and "21.719" in especiales[0].message


# --------------------------------------------------------------------- llm


class _FakeLlm:
    def __init__(self) -> None:
        self.recibidas: list[str] = []

    def classify(self, asset: DataAsset, columns: list[Column]) -> list[Classification]:
        self.recibidas = [c.name for c in columns]
        return [
            Classification(
                column_name="campo_misterioso",
                category="contact",
                label="Dato de contacto",
                sensitivity=Sensitivity.CONFIDENTIAL,
                policy_tag="person.contact",
                confidence=0.82,
                source="llm",
            )
        ]


def test_el_llm_solo_ve_columnas_desconocidas() -> None:
    fake = _FakeLlm()
    clf = PrivacyClassifier(llm=fake)
    a = asset(
        "commerce_silver",
        "mst_customer",
        [Column("email", "STRING"), Column("campo_misterioso", "STRING"), Column("order_quantity", "INT64")],
    )
    resultados = clf.classify(a)
    assert "email" not in fake.recibidas
    assert "campo_misterioso" in fake.recibidas
    assert resultados[1].category == "contact"
    assert resultados[1].source == "llm"


def test_si_el_llm_falla_el_gate_sigue_funcionando() -> None:
    class Roto:
        def classify(self, asset, columns):
            raise RuntimeError("cuota agotada")

    clf = PrivacyClassifier(llm=Roto())
    a = asset("commerce_silver", "mst_customer", [Column("email", "STRING"), Column("zzz", "STRING")])
    resultados = clf.classify(a)
    assert resultados[0].category == "contact"
    assert "cuota agotada" in resultados[1].rationale


# ------------------------------------------------------- orden de sensibilidad


def test_el_orden_de_sensibilidad_no_es_alfabetico() -> None:
    """`Sensitivity` hereda de str: sin operadores propios, INTERNAL >= CONFIDENTIAL
    daria True por orden alfabetico y las columnas INTERNAL exigirian policy tag."""
    assert Sensitivity.RESTRICTED > Sensitivity.CONFIDENTIAL
    assert Sensitivity.CONFIDENTIAL > Sensitivity.INTERNAL
    assert Sensitivity.INTERNAL > Sensitivity.PUBLIC
    assert not (Sensitivity.INTERNAL >= Sensitivity.CONFIDENTIAL)


def test_dato_comercial_interno_no_exige_policy_tag(engine: PrivacyEngine) -> None:
    a = asset("commerce_gold", "dmt_margin_daily", [Column("cost_amount", "NUMERIC")])
    checks = {h.check for h in engine.evaluate(a)}
    assert "privacy.missing_policy_tag" not in checks


# ------------------------------------------------------ boveda de identidad


def _boveda(**labels) -> DataAsset:
    a = asset(
        "commerce_silver",
        "mst_customer_identity",
        [
            Column("customer_key", "STRING", policy_tag="person.reference"),
            Column("customer_email", "STRING", policy_tag="person.contact"),
            Column("customer_full_name", "STRING", policy_tag="person.name"),
        ],
    )
    a.labels = {"data_vault": "true", **labels}
    return a


def test_boveda_etiquetada_no_exige_seudonimizacion(engine: PrivacyEngine) -> None:
    """Seudonimizar una boveda de identidad la deja sin proposito: existe para
    concentrar el dato identificable bajo una unica lista de acceso."""
    assert engine.evaluate(_boveda()) == []


def test_boveda_sin_policy_tags_no_obtiene_la_exencion(engine: PrivacyEngine) -> None:
    """La exencion se paga con etiquetado. Una boveda sin tags es solo una tabla
    con datos personales."""
    a = _boveda()
    a.columns[1].policy_tag = None
    hallazgos = engine.evaluate(a)
    assert not engine.is_vault(a)
    assert any(h.check == "privacy.missing_policy_tag" for h in hallazgos)
    assert any(h.check == "privacy.treatment" for h in hallazgos)


def test_sin_label_no_hay_exencion(engine: PrivacyEngine) -> None:
    a = _boveda()
    a.labels = {}
    assert not engine.is_vault(a)


def test_dominio_de_correo_no_es_dato_de_contacto(classifier: PrivacyClassifier) -> None:
    """`email_domain_name` es el resultado de haber quitado el correo: marcarla
    castigaria la mitigacion correcta."""
    a = asset("commerce_silver", "mst_customer", [Column("email_domain_name", "STRING")])
    assert classifier.classify_column(a, a.columns[0]).category is None
