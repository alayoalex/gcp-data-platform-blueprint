"""Contratos de datos: la promesa explicita de un producto de datos.

Un contrato declara que expone un activo, con que grano, con que frescura, quien lo
mantiene y quien lo consume. Sirve para tres cosas que sin el no se pueden hacer:

- **Fiscalizar el esquema**: la tabla real debe coincidir con lo prometido.
- **Detectar breaking changes**: quitar o retipar una columna prometida rompe a los
  consumidores declarados, y el gate lo sabe antes del merge, no despues del incidente.
- **Alimentar agentes**: un agente de IA que consulta el warehouse necesita saber que
  significa cada campo y si puede confiar en el. El contrato es esa fuente.

Sin contrato, un producto de datos es solo una tabla con buen nombre.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from faro_governance.models import (
    Assessment,
    Column,
    DataAsset,
    Layer,
    Sensitivity,
    Severity,
)

REQUIRED_METADATA = ["name", "product", "version", "owner"]
REQUIRED_SPEC = ["asset", "layer", "description", "grain", "freshness", "retention"]

#: Cambios de tipo que NO rompen al consumidor: el valor sigue cabiendo.
WIDENING_CASTS = {
    ("INT64", "NUMERIC"),
    ("INT64", "BIGNUMERIC"),
    ("INT64", "FLOAT64"),
    ("NUMERIC", "BIGNUMERIC"),
    ("NUMERIC", "FLOAT64"),
    ("DATE", "DATETIME"),
    ("DATE", "TIMESTAMP"),
    ("DATETIME", "TIMESTAMP"),
}


@dataclass
class DataContract:
    path: Path
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> DataContract:
        p = Path(path)
        return cls(path=p, raw=yaml.safe_load(p.read_text(encoding="utf-8")) or {})

    @property
    def metadata(self) -> dict[str, Any]:
        return self.raw.get("metadata", {})

    @property
    def spec(self) -> dict[str, Any]:
        return self.raw.get("spec", {})

    @property
    def name(self) -> str:
        return self.metadata.get("name", self.path.stem)

    @property
    def asset_ref(self) -> str:
        return self.spec.get("asset", "")

    @property
    def version(self) -> str:
        return str(self.metadata.get("version", "0.0.0"))

    @property
    def columns(self) -> list[dict[str, Any]]:
        return self.spec.get("schema", [])

    def column(self, name: str) -> dict[str, Any] | None:
        return next((c for c in self.columns if c.get("name") == name), None)


def load_contracts(directory: str | Path) -> dict[str, DataContract]:
    """Indexa los contratos de un directorio por referencia de activo."""
    out: dict[str, DataContract] = {}
    for path in sorted(Path(directory).glob("*.y*ml")):
        contract = DataContract.load(path)
        if contract.asset_ref:
            out[contract.asset_ref] = contract
    return out


class ContractEngine:
    def __init__(self, *, require_contract_for: tuple[Layer, ...] = (Layer.GOLD, Layer.FEATURE)) -> None:
        self.require_contract_for = require_contract_for

    def evaluate(self, asset: DataAsset, contract: DataContract | None) -> list[Assessment]:
        if contract is None:
            if asset.layer in self.require_contract_for:
                return [
                    self._fail(
                        asset,
                        asset.name,
                        "contract.missing",
                        f"El activo esta en la capa {asset.layer.value} y no tiene contrato de datos. "
                        "Todo lo que un equipo externo puede consumir debe estar bajo contrato.",
                        f"Crear contracts/{asset.name}.yaml declarando grano, frescura, "
                        "retencion y consumidores.",
                        Severity.BLOCK,
                    )
                ]
            return []

        findings: list[Assessment] = []
        findings.extend(self._check_metadata(asset, contract))
        findings.extend(self._check_schema(asset, contract))
        findings.extend(self._check_classification(asset, contract))
        return findings

    # ------------------------------------------------------------- metadatos

    def _check_metadata(self, asset: DataAsset, contract: DataContract) -> list[Assessment]:
        missing_meta = [k for k in REQUIRED_METADATA if not contract.metadata.get(k)]
        missing_spec = [k for k in REQUIRED_SPEC if not contract.spec.get(k)]
        missing = [f"metadata.{k}" for k in missing_meta] + [f"spec.{k}" for k in missing_spec]
        if not missing:
            return []
        return [
            self._fail(
                asset,
                contract.name,
                "contract.incomplete",
                "El contrato no declara campos obligatorios: " + ", ".join(missing) + ".",
                "Completar el contrato. Un contrato sin duenio ni frescura no se puede operar ni reclamar.",
                Severity.BLOCK,
                source_path=str(contract.path),
            )
        ]

    # ---------------------------------------------------------------- esquema

    def _check_schema(self, asset: DataAsset, contract: DataContract) -> list[Assessment]:
        findings: list[Assessment] = []
        actual = {c.name: c for c in asset.columns}
        declared = {c["name"]: c for c in contract.columns if c.get("name")}

        for name, spec in declared.items():
            column = actual.get(name)
            if column is None:
                consumers = ", ".join(contract.spec.get("consumers", [])) or "los consumidores declarados"
                findings.append(
                    self._fail(
                        asset,
                        f"{asset.name}.{name}",
                        "contract.breaking_change",
                        f"El contrato promete la columna '{name}' y el activo no la expone. "
                        "Esto rompe a todo consumidor declarado.",
                        f"Restaurar '{name}', o publicar una version mayor del contrato "
                        f"y notificar a {consumers}.",
                        Severity.BLOCK,
                        source_path=str(contract.path),
                    )
                )
                continue
            findings.extend(self._check_type(asset, contract, column, spec))

        for name in actual.keys() - declared.keys():
            findings.append(
                self._fail(
                    asset,
                    f"{asset.name}.{name}",
                    "contract.undeclared_column",
                    f"La columna '{name}' existe en el activo pero no en el contrato.",
                    "Agregarla al contrato (con descripcion y clasificacion) o quitarla del modelo. "
                    "Una columna fuera de contrato no tiene duenio ni garantia.",
                    Severity.WARN,
                    source_path=str(contract.path),
                )
            )

        for column_name in contract.spec.get("grain", []):
            if column_name not in actual:
                findings.append(
                    self._fail(
                        asset,
                        f"{asset.name}.{column_name}",
                        "contract.invalid_grain",
                        f"El grano declarado incluye '{column_name}', que no existe en el activo.",
                        "Corregir spec.grain para que refleje la llave real de la tabla.",
                        Severity.BLOCK,
                        source_path=str(contract.path),
                    )
                )

        return findings

    def _check_type(
        self,
        asset: DataAsset,
        contract: DataContract,
        column: Column,
        spec: dict[str, Any],
    ) -> list[Assessment]:
        promised = str(spec.get("type", "")).upper()
        actual = column.data_type.upper().split("(")[0].strip()
        if not promised or promised == actual:
            return []
        if (actual, promised) in WIDENING_CASTS or (promised, actual) in WIDENING_CASTS:
            return [
                self._fail(
                    asset,
                    f"{asset.name}.{column.name}",
                    "contract.type_drift",
                    f"'{column.name}' es {actual} y el contrato promete {promised}. "
                    "El cambio es compatible pero el contrato quedo desactualizado.",
                    f"Actualizar el contrato a {actual} y subir la version menor.",
                    Severity.WARN,
                    source_path=str(contract.path),
                )
            ]
        return [
            self._fail(
                asset,
                f"{asset.name}.{column.name}",
                "contract.breaking_change",
                f"'{column.name}' cambio de {promised} (contrato) a {actual} (activo). "
                "Es un cambio incompatible para los consumidores.",
                "Subir version mayor del contrato, anunciar la deprecacion y mantener la "
                "columna anterior durante la ventana acordada.",
                Severity.BLOCK,
                source_path=str(contract.path),
            )
        ]

    # --------------------------------------------------------- clasificacion

    def _check_classification(self, asset: DataAsset, contract: DataContract) -> list[Assessment]:
        """Contrasta lo que el contrato declara con lo que el clasificador detecta.

        Importa el sentido del desacuerdo: declarar de MENOS es el riesgo real
        (un dato personal viajando como si fuera publico). Declarar de mas es
        conservador y solo se informa.
        """
        from faro_governance.privacy import PrivacyClassifier

        classifier = PrivacyClassifier()
        findings: list[Assessment] = []
        for column in asset.columns:
            spec = contract.column(column.name)
            if spec is None:
                continue
            declared_raw = str(spec.get("classification", "")).upper()
            if declared_raw not in Sensitivity.__members__:
                continue
            declared = Sensitivity[declared_raw]
            detected = classifier.classify_column(asset, column)
            if detected.category is None:
                continue
            if detected.sensitivity > declared:
                findings.append(
                    self._fail(
                        asset,
                        f"{asset.name}.{column.name}",
                        "contract.under_classified",
                        f"El contrato declara '{column.name}' como {declared.value} pero se detecto "
                        f"{detected.label} ({detected.sensitivity.value}, confianza {detected.confidence}).",
                        f"Reclasificar a {detected.sensitivity.value} y aplicar el policy tag "
                        f"'{detected.policy_tag}', o justificar la excepcion en el contrato.",
                        Severity.BLOCK,
                        source_path=str(contract.path),
                    )
                )
        return findings

    # ----------------------------------------------------------------- utils

    @staticmethod
    def _fail(
        asset: DataAsset,
        target: str,
        check: str,
        message: str,
        recommendation: str,
        severity: Severity,
        source_path: str | None = None,
    ) -> Assessment:
        return Assessment(
            asset_id=asset.asset_id,
            target=target,
            check=check,
            score=0.0,
            threshold=1.0,
            severity=severity,
            message=message,
            recommendation=recommendation,
            source_path=source_path or asset.source_path,
        )


def diff_contracts(previous: DataContract, current: DataContract) -> list[str]:
    """Lista los cambios incompatibles entre dos versiones de un contrato.

    Se usa en el gate contra la version del contrato en la rama base, para exigir
    un salto de version mayor cuando corresponde.
    """
    breaking: list[str] = []
    old = {c["name"]: c for c in previous.columns if c.get("name")}
    new = {c["name"]: c for c in current.columns if c.get("name")}

    for name in old.keys() - new.keys():
        breaking.append(f"columna eliminada: {name}")
    for name in old.keys() & new.keys():
        old_type = str(old[name].get("type", "")).upper()
        new_type = str(new[name].get("type", "")).upper()
        if old_type and new_type and old_type != new_type and (old_type, new_type) not in WIDENING_CASTS:
            breaking.append(f"tipo incompatible en {name}: {old_type} -> {new_type}")
        if old[name].get("required") is False and new[name].get("required") is True:
            breaking.append(f"columna ahora obligatoria: {name}")
    if previous.spec.get("grain") != current.spec.get("grain"):
        breaking.append(
            f"cambio de grano: {previous.spec.get('grain')} -> {current.spec.get('grain')}"
        )
    return breaking


def major(version: str) -> int:
    try:
        return int(str(version).split(".")[0])
    except (ValueError, IndexError):
        return 0
