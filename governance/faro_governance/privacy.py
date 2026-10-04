"""Clasificacion de privacidad de columnas.

Dos responsabilidades distintas que conviene no mezclar:

1. **Clasificar**: dada una columna, decir que categoria de dato personal contiene
   y con que nivel de sensibilidad. Esto es deteccion.
2. **Fiscalizar**: dada esa clasificacion y la capa donde vive la columna, decir si
   el tratamiento aplicado es suficiente. Esto es politica.

La primera admite un LLM (el catalogo de patrones nunca cubre todo). La segunda no:
si una columna RESTRICTED aparece sin seudonimizar en gold, eso no se opina, se bloquea.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import yaml

from faro_governance.models import (
    Assessment,
    AssetType,
    Column,
    DataAsset,
    Layer,
    Sensitivity,
    Severity,
)

DEFAULT_CATALOG_PATH = Path(__file__).parent / "rules" / "privacy.yaml"


@dataclass
class Classification:
    column_name: str
    category: str | None
    label: str | None
    sensitivity: Sensitivity
    policy_tag: str | None
    confidence: float
    special_category: bool = False
    rationale: str = ""
    matched_pattern: str | None = None
    source: str = "rules"

    @property
    def is_personal_data(self) -> bool:
        return self.category is not None and self.sensitivity >= Sensitivity.CONFIDENTIAL

    @property
    def needs_human_review(self) -> bool:
        return self.category is not None and self.confidence < 0.70


class LlmClassifier(Protocol):
    """Contrato del clasificador LLM opcional.

    Se invoca SOLO para columnas que el catalogo de reglas no reconocio. Asi el costo
    y la latencia del LLM escalan con lo desconocido, no con el tamanio del esquema:
    en un modelo maduro el 90% de las columnas las resuelve el catalogo gratis.
    """

    def classify(self, asset: DataAsset, columns: list[Column]) -> list[Classification]: ...


@dataclass
class PrivacyCatalog:
    raw: dict[str, Any]
    _compiled: list[tuple[dict[str, Any], list[re.Pattern[str]]]] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path | None = None) -> PrivacyCatalog:
        raw = yaml.safe_load(Path(path or DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))
        catalog = cls(raw)
        catalog._compiled = [
            (entry, [re.compile(p, re.I) for p in entry["patterns"]])
            for entry in raw["categories"]
        ]
        return catalog

    @property
    def exclusions(self) -> list[re.Pattern[str]]:
        return [re.compile(p, re.I) for p in self.raw.get("exclusions", [])]

    @property
    def review_threshold(self) -> float:
        return float(self.raw.get("review_threshold", 0.7))

    def treatment(self, sensitivity: Sensitivity, layer: Layer) -> str:
        matrix = self.raw["treatment_matrix"].get(sensitivity.value, {})
        return matrix.get(layer.value, "ALLOW")


@lru_cache(maxsize=4)
def _default_catalog() -> PrivacyCatalog:
    return PrivacyCatalog.load()


class PrivacyClassifier:
    def __init__(
        self,
        catalog: PrivacyCatalog | None = None,
        llm: LlmClassifier | None = None,
    ) -> None:
        self.catalog = catalog or _default_catalog()
        self.llm = llm
        self._exclusions = self.catalog.exclusions

    # ------------------------------------------------------------ clasificar

    def classify_column(self, asset: DataAsset, column: Column) -> Classification:
        name = column.name.lower()

        if any(rx.search(name) for rx in self._exclusions):
            return self._not_personal(column, "Excluido por patron de la politica (agregado o booleano).")

        for entry, patterns in self.catalog._compiled:
            for pattern in patterns:
                if pattern.search(name):
                    return Classification(
                        column_name=column.name,
                        category=entry["id"],
                        label=entry["label"],
                        sensitivity=Sensitivity(entry["sensitivity"]),
                        policy_tag=entry["policy_tag"],
                        confidence=self._confidence(asset, entry),
                        special_category=entry.get("special_category", False),
                        rationale=entry.get("rationale", "").strip(),
                        matched_pattern=pattern.pattern,
                        source="rules",
                    )

        return self._not_personal(column, "Ningun patron del catalogo calzo con el nombre.")

    def classify(self, asset: DataAsset) -> list[Classification]:
        """Clasifica todas las columnas, delegando al LLM solo las desconocidas."""
        results = [self.classify_column(asset, col) for col in asset.columns]
        if self.llm is None:
            return results

        unknown_idx = [i for i, r in enumerate(results) if r.category is None]
        if not unknown_idx:
            return results
        unknown_cols = [asset.columns[i] for i in unknown_idx]
        try:
            llm_results = self.llm.classify(asset, unknown_cols)
        except Exception as exc:  # el LLM es un enriquecedor, no una dependencia dura
            for i in unknown_idx:
                results[i].rationale += f" (clasificacion LLM no disponible: {exc})"
            return results
        by_name = {r.column_name: r for r in llm_results}
        for i in unknown_idx:
            enriched = by_name.get(asset.columns[i].name)
            if enriched is not None and enriched.category is not None:
                results[i] = enriched
        return results

    def _confidence(self, asset: DataAsset, entry: dict[str, Any]) -> float:
        """Ajusta la confianza segun el contexto del activo.

        Una columna `name` en `customer_silver.mst_customer` es casi con certeza el
        nombre de una persona; la misma columna en `catalog_silver.mst_product` es el
        nombre de un producto. El contexto es la diferencia entre un hallazgo util
        y un falso positivo que erosiona la confianza del equipo en el gate.
        """
        confidence = 0.75
        boosters = self.catalog.raw.get("context_boosters", {})
        haystack_dataset = asset.dataset_id.lower()
        haystack_table = asset.name.lower()
        if any(word in haystack_dataset for word in boosters.get("dataset", [])):
            confidence += 0.15
        if any(word in haystack_table for word in boosters.get("table", [])):
            confidence += 0.15
        # Los identificadores fuertes no dependen del contexto: un RUT es un RUT.
        if entry["sensitivity"] == Sensitivity.RESTRICTED.value:
            confidence = max(confidence, 0.90)
        return round(min(confidence, 1.0), 2)

    @staticmethod
    def _not_personal(column: Column, why: str) -> Classification:
        return Classification(
            column_name=column.name,
            category=None,
            label=None,
            sensitivity=Sensitivity.INTERNAL,
            policy_tag=None,
            confidence=1.0,
            rationale=why,
        )


class PrivacyEngine:
    """Convierte clasificaciones en veredictos sobre el activo."""

    def __init__(self, classifier: PrivacyClassifier | None = None) -> None:
        self.classifier = classifier or PrivacyClassifier()
        self.catalog = self.classifier.catalog

    def is_vault(self, asset: DataAsset) -> bool:
        """Indica si el activo es una boveda de identidad declarada.

        Una boveda existe para concentrar el dato identificable, asi que exigirle
        seudonimizacion la dejaria sin proposito. A cambio, cada columna clasificada
        debe llevar su policy tag: el control se traslada de la forma del dato al
        permiso de lectura. Sin esa contrapartida no hay exencion.
        """
        label = self.catalog.raw.get("vault_label", "data_vault")
        if asset.labels.get(label, "").lower() != "true":
            return False
        for column, klass in zip(asset.columns, self.classifier.classify(asset), strict=True):
            if klass.sensitivity >= Sensitivity.CONFIDENTIAL and not column.policy_tag:
                return False
        return True

    def evaluate(self, asset: DataAsset) -> list[Assessment]:
        if asset.asset_type is AssetType.DATASET:
            return []
        layer = asset.layer
        vault = self.is_vault(asset)
        assessments: list[Assessment] = []

        for column, klass in zip(asset.columns, self.classifier.classify(asset), strict=True):
            if klass.category is None:
                continue
            entry = self._entry(klass.category)

            # 1. La columna no debe existir en esta capa en absoluto.
            forbidden = entry.get("forbidden_in", [])
            if layer.value in forbidden:
                assessments.append(
                    self._fail(
                        asset,
                        column,
                        klass,
                        check="privacy.forbidden",
                        message=(
                            f"'{column.name}' se clasifico como {klass.label} "
                            f"({klass.category}) y esta prohibida en toda la plataforma analitica."
                        ),
                        recommendation=(
                            "Eliminar la columna del modelo. Nunca debe salir del "
                            "sistema transaccional."
                        ),
                        severity=Severity.BLOCK,
                    )
                )
                continue

            # 2. La columna necesita policy tag y no lo tiene.
            treatment = self.catalog.treatment(klass.sensitivity, layer)
            if klass.sensitivity >= Sensitivity.CONFIDENTIAL and not column.policy_tag:
                assessments.append(
                    self._fail(
                        asset,
                        column,
                        klass,
                        check="privacy.missing_policy_tag",
                        message=(
                            f"'{column.name}' contiene {klass.label} ({klass.sensitivity.value}) "
                            f"y no tiene policy tag asignado."
                        ),
                        recommendation=f"Asignar el policy tag '{klass.policy_tag}'.",
                        severity=Severity.BLOCK if layer in (Layer.GOLD, Layer.FEATURE) else Severity.WARN,
                    )
                )

            # 3. El tratamiento exigido por la capa no se cumple.
            #    Una boveda declarada y correctamente etiquetada queda exenta: su
            #    control es el policy tag, verificado en el paso anterior.
            if vault:
                pass
            elif treatment == "DROP":
                assessments.append(
                    self._fail(
                        asset,
                        column,
                        klass,
                        check="privacy.treatment",
                        message=(
                            f"'{column.name}' es {klass.sensitivity.value} y la politica no permite "
                            f"dato identificable en la capa {layer.value}."
                        ),
                        recommendation=(
                            "Seudonimizar en silver y exponer solo la clave seudonimizada aguas abajo, "
                            "o agregar el dato hasta un grano no identificable."
                        ),
                        severity=Severity.BLOCK,
                    )
                )
            elif treatment in ("HASH", "MASK") and not self._looks_treated(column, treatment):
                assessments.append(
                    self._fail(
                        asset,
                        column,
                        klass,
                        check="privacy.treatment",
                        message=(
                            f"'{column.name}' es {klass.sensitivity.value} en capa {layer.value}: "
                            f"la politica exige {treatment} y el nombre no lo evidencia."
                        ),
                        recommendation=(
                            f"Aplicar {treatment} y renombrar a "
                            f"'{self._treated_name(column.name, treatment)}' para que el tratamiento "
                            "sea legible en el esquema."
                        ),
                        severity=Severity.WARN,
                    )
                )

            # 4. Dato sensible del Art. 2 g): siempre deja rastro, aunque pase.
            if klass.special_category:
                assessments.append(
                    Assessment(
                        asset_id=asset.asset_id,
                        target=f"{asset.name}.{column.name}",
                        check="privacy.special_category",
                        score=0.0,
                        threshold=1.0,
                        severity=Severity.BLOCK,
                        message=(
                            f"'{column.name}' es un dato sensible (Art. 2 g, Ley 21.719). "
                            "Requiere base de licitud reforzada y aprobacion del DPO."
                        ),
                        recommendation=(
                            "Adjuntar la evaluacion de impacto y la base de licitud "
                            "al contrato de datos."
                        ),
                        source_path=asset.source_path,
                    )
                )

            if klass.needs_human_review:
                assessments.append(
                    Assessment(
                        asset_id=asset.asset_id,
                        target=f"{asset.name}.{column.name}",
                        check="privacy.review",
                        score=klass.confidence,
                        threshold=self.catalog.review_threshold,
                        severity=Severity.INFO,
                        message=(
                            f"Clasificacion de '{column.name}' como {klass.label} con confianza "
                            f"{klass.confidence}. Confirmar con el duenio del dato."
                        ),
                        source_path=asset.source_path,
                    )
                )

        return assessments

    def _entry(self, category_id: str) -> dict[str, Any]:
        return next(e for e in self.catalog.raw["categories"] if e["id"] == category_id)

    @staticmethod
    def _looks_treated(column: Column, treatment: str) -> bool:
        markers = {"HASH": ("_hash", "_hashed", "_pseudonym", "_sk"), "MASK": ("_masked", "_redacted")}
        name = column.name.lower()
        return any(name.endswith(m) for m in markers.get(treatment, ()))

    @staticmethod
    def _treated_name(name: str, treatment: str) -> str:
        return f"{name}{'_hash' if treatment == 'HASH' else '_masked'}"

    @staticmethod
    def _fail(
        asset: DataAsset,
        column: Column,
        klass: Classification,
        *,
        check: str,
        message: str,
        recommendation: str,
        severity: Severity,
    ) -> Assessment:
        return Assessment(
            asset_id=asset.asset_id,
            target=f"{asset.name}.{column.name}",
            check=check,
            score=0.0,
            threshold=1.0,
            severity=severity,
            message=message,
            recommendation=recommendation,
            source_path=asset.source_path,
        )
