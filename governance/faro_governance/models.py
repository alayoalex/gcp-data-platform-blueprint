"""Modelo de dominio de Faro.

Un activo de datos (`DataAsset`) es la unidad que la plataforma gobierna: una tabla,
vista o dataset de BigQuery con sus columnas. Todo lo demas -- naming, privacidad,
contratos -- produce `Assessment` sobre esos activos.

El modelo es deliberadamente agnostico del motor: no importa BigQuery ni Dataform.
Los adaptadores de `faro_governance.parser` traducen la salida de esas herramientas
a estos objetos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AssetType(str, Enum):
    DATASET = "DATASET"
    TABLE = "TABLE"
    VIEW = "VIEW"
    INCREMENTAL = "INCREMENTAL"

    @property
    def is_relation(self) -> bool:
        return self is not AssetType.DATASET


class Layer(str, Enum):
    """Capa medallion del activo.

    La capa condiciona que estructura de nombre se espera: un activo bronze debe
    delatar su sistema de origen, uno gold debe delatar su producto de datos.
    """

    BRONZE = "BRONZE"
    SILVER = "SILVER"
    GOLD = "GOLD"
    FEATURE = "FEATURE"
    UNKNOWN = "UNKNOWN"


class Severity(str, Enum):
    BLOCK = "BLOCK"
    WARN = "WARN"
    INFO = "INFO"


class Sensitivity(str, Enum):
    """Nivel de sensibilidad. Ordenado de menor a mayor restriccion."""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"

    @property
    def rank(self) -> int:
        return _SENSITIVITY_ORDER.index(self)

    # Hereda de `str`, asi que sin estos metodos los operadores de orden comparan
    # alfabeticamente y `INTERNAL >= CONFIDENTIAL` resulta True. Se definen los
    # cuatro explicitamente: `functools.total_ordering` no sirve aqui porque la
    # clase base ya los provee y no se considerarian faltantes.
    def __lt__(self, other: object) -> bool:  # type: ignore[override]
        if not isinstance(other, Sensitivity):
            return NotImplemented
        return self.rank < other.rank

    def __le__(self, other: object) -> bool:  # type: ignore[override]
        if not isinstance(other, Sensitivity):
            return NotImplemented
        return self.rank <= other.rank

    def __gt__(self, other: object) -> bool:  # type: ignore[override]
        if not isinstance(other, Sensitivity):
            return NotImplemented
        return self.rank > other.rank

    def __ge__(self, other: object) -> bool:  # type: ignore[override]
        if not isinstance(other, Sensitivity):
            return NotImplemented
        return self.rank >= other.rank


_SENSITIVITY_ORDER = [
    Sensitivity.PUBLIC,
    Sensitivity.INTERNAL,
    Sensitivity.CONFIDENTIAL,
    Sensitivity.RESTRICTED,
]


@dataclass
class Column:
    name: str
    data_type: str = "STRING"
    description: str | None = None
    mode: str = "NULLABLE"
    policy_tag: str | None = None
    #: Categoria de privacidad asignada manualmente en el contrato, si la hay.
    declared_category: str | None = None

    @property
    def has_description(self) -> bool:
        return bool(self.description and self.description.strip())


@dataclass
class DataAsset:
    project_id: str
    dataset_id: str
    name: str
    asset_type: AssetType = AssetType.TABLE
    description: str | None = None
    columns: list[Column] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    #: Ruta del archivo fuente que declara el activo (para anclar hallazgos en el merge request).
    source_path: str | None = None
    _layer_override: Layer | None = None

    @property
    def asset_id(self) -> str:
        if self.asset_type is AssetType.DATASET:
            return f"bigquery://{self.project_id}.{self.dataset_id}"
        return f"bigquery://{self.project_id}.{self.dataset_id}.{self.name}"

    @property
    def layer(self) -> Layer:
        """Infiere la capa del dataset o de las labels.

        El orden importa: una label explicita `layer` gana sobre la convencion de
        nombre del dataset, porque la label es una declaracion del equipo duenio y
        la convencion es solo una heuristica.
        """
        if self._layer_override is not None:
            return self._layer_override
        declared = self.labels.get("layer", "").upper()
        if declared in Layer.__members__:
            return Layer[declared]
        haystack = f"{self.dataset_id}"
        for layer in (Layer.BRONZE, Layer.SILVER, Layer.GOLD, Layer.FEATURE):
            if re.search(rf"(^|[_-]){layer.value.lower()}([_-]|$)", haystack, re.I):
                return layer
        return Layer.UNKNOWN

    @layer.setter
    def layer(self, value: Layer) -> None:
        self._layer_override = value

    @property
    def has_description(self) -> bool:
        return bool(self.description and self.description.strip())

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


@dataclass
class CriterionScore:
    """Puntaje de un criterio individual dentro de una evaluacion."""

    criterion: str
    weight: float
    score: float
    reasoning: str = ""

    @property
    def weighted(self) -> float:
        return self.weight * self.score


@dataclass
class Assessment:
    """Resultado de evaluar un objetivo (tabla, columna o dataset) bajo una politica."""

    asset_id: str
    target: str
    check: str
    score: float
    threshold: float
    breakdown: list[CriterionScore] = field(default_factory=list)
    severity: Severity = Severity.WARN
    message: str = ""
    recommendation: str | None = None
    source_path: str | None = None

    @property
    def passed(self) -> bool:
        return self.score >= self.threshold

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "target": self.target,
            "check": self.check,
            "score": round(self.score, 4),
            "threshold": self.threshold,
            "passed": self.passed,
            "severity": self.severity.value,
            "message": self.message,
            "recommendation": self.recommendation,
            "source_path": self.source_path,
            "breakdown": [
                {
                    "criterion": c.criterion,
                    "weight": c.weight,
                    "score": round(c.score, 4),
                    "reasoning": c.reasoning,
                }
                for c in self.breakdown
            ],
        }


@dataclass
class Report:
    """Conjunto de evaluaciones de una corrida del gate."""

    assessments: list[Assessment] = field(default_factory=list)
    assets_evaluated: int = 0

    def extend(self, items: list[Assessment]) -> None:
        self.assessments.extend(items)

    @property
    def failures(self) -> list[Assessment]:
        return [a for a in self.assessments if not a.passed]

    @property
    def blocking(self) -> list[Assessment]:
        return [a for a in self.failures if a.severity is Severity.BLOCK]

    @property
    def should_block(self) -> bool:
        return bool(self.blocking)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "assets_evaluated": self.assets_evaluated,
                "checks_run": len(self.assessments),
                "failures": len(self.failures),
                "blocking": len(self.blocking),
                "status": "BLOCKED" if self.should_block else "PASSED",
            },
            "assessments": [a.to_dict() for a in self.assessments],
        }
