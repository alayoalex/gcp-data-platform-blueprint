"""Motor de evaluacion de nombres.

Deliberadamente determinista: las mismas entradas dan siempre el mismo puntaje.
Eso es lo que permite usarlo como gate de CI -- un gate que a veces deja pasar y a
veces no el mismo nombre no es un gate, es una loteria, y los equipos aprenden a
reintentar el pipeline en vez de corregir el nombre.

El LLM entra despues, no antes: `faro_governance.llm` puede enriquecer los hallazgos
con explicaciones y nombres alternativos en lenguaje natural, pero la decision de
bloquear o no sale de aqui.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from faro_governance.models import (
    Assessment,
    AssetType,
    Column,
    CriterionScore,
    DataAsset,
    Layer,
    Severity,
)

DEFAULT_POLICY_PATH = Path(__file__).parent / "rules" / "naming.yaml"

SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")


@dataclass
class NamingPolicy:
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path | None = None) -> NamingPolicy:
        return cls(yaml.safe_load(Path(path or DEFAULT_POLICY_PATH).read_text(encoding="utf-8")))

    def weights(self, kind: str) -> dict[str, float]:
        return self.raw["weights"][kind]

    def threshold(self, kind: str) -> float:
        return float(self.raw["thresholds"][kind])

    def severity(self, layer: Layer) -> Severity:
        return Severity(self.raw["severity_by_layer"].get(layer.value, "WARN"))

    @property
    def abbreviations(self) -> dict[str, str]:
        return self.raw["abbreviations"]

    @property
    def allowed_acronyms(self) -> set[str]:
        return set(self.raw["allowed_acronyms"])

    @property
    def noise_tokens(self) -> set[str]:
        return set(self.raw["noise_tokens"])

    @property
    def system_columns(self) -> set[str]:
        return set(self.raw["system_columns"])

    @property
    def data_categories(self) -> dict[str, dict[str, Any]]:
        return self.raw["data_categories"]

    def layer_prefixes(self, layer: Layer) -> list[str]:
        return self.raw["layer_structure"].get(layer.value, {}).get("prefixes", [])

    def layer_pattern(self, layer: Layer) -> str:
        return self.raw["layer_structure"].get(layer.value, {}).get("pattern", "")


@lru_cache(maxsize=4)
def _default_policy() -> NamingPolicy:
    return NamingPolicy.load()


def tokenize(name: str) -> list[str]:
    return [t for t in re.split(r"[_\s\-]+", name.strip().lower()) if t]


class NamingEngine:
    def __init__(self, policy: NamingPolicy | None = None) -> None:
        self.policy = policy or _default_policy()

    # ---------------------------------------------------------------- publico

    def evaluate(self, asset: DataAsset) -> list[Assessment]:
        """Evalua el activo y, si es una relacion, cada una de sus columnas."""
        if asset.asset_type is AssetType.DATASET:
            return [self._evaluate_dataset(asset)]
        results = [self._evaluate_relation(asset)]
        results.extend(self._evaluate_column(asset, col) for col in asset.columns)
        return results

    # ------------------------------------------------------------- criterios

    def _snake_case(self, name: str) -> tuple[float, str]:
        if SNAKE_CASE_RE.match(name):
            return 1.0, "snake_case correcto."
        problems = []
        score = 1.0
        if any(c.isupper() for c in name):
            problems.append("contiene mayusculas")
            score -= 0.6
        if re.search(r"[^a-zA-Z0-9_]", name):
            problems.append("contiene caracteres no alfanumericos")
            score -= 0.6
        if name[:1].isdigit():
            problems.append("empieza con digito")
            score -= 0.4
        if "__" in name or name.startswith("_") or name.endswith("_"):
            problems.append("guiones bajos redundantes o en los extremos")
            score -= 0.3
        return max(score, 0.0), "Problemas: " + ", ".join(problems) + "."

    def _full_words(self, name: str) -> tuple[float, str]:
        tokens = tokenize(name)
        if not tokens:
            return 0.0, "Nombre vacio."
        offenders: list[str] = []
        for token in tokens:
            if token in self.policy.allowed_acronyms:
                continue
            if token in self.policy.abbreviations:
                offenders.append(f"{token} -> {self.policy.abbreviations[token]}")
            elif len(token) <= 2 and not token.isdigit():
                offenders.append(f"{token} (token de {len(token)} letras sin significado declarado)")
        score = 1.0 - (len(offenders) / len(tokens))
        if not offenders:
            return 1.0, "Todos los tokens son palabras completas o siglas autorizadas."
        return max(score, 0.0), "Abreviaturas a expandir: " + "; ".join(offenders) + "."

    def _no_redundancy(self, name: str, parent: str | None = None) -> tuple[float, str]:
        tokens = tokenize(name)
        if not tokens:
            return 0.0, "Nombre vacio."
        problems: list[str] = []
        score = 1.0
        repeated = {t for t in tokens if tokens.count(t) > 1}
        if repeated:
            problems.append("tokens repetidos dentro del nombre: " + ", ".join(sorted(repeated)))
            score -= 0.5
        noise = sorted(set(tokens) & self.policy.noise_tokens)
        if noise:
            problems.append("tokens sin aporte semantico: " + ", ".join(noise))
            score -= 0.3 * len(noise)
        if not problems:
            return 1.0, "Sin redundancia."
        return max(score, 0.0), "Problemas: " + "; ".join(problems) + "."

    def _readable(self, name: str) -> tuple[float, str]:
        cfg = self.policy.raw["readable"]
        tokens = tokenize(name)
        score = 1.0
        problems = []
        if len(name) > cfg["max_length"]:
            problems.append(f"{len(name)} caracteres (maximo {cfg['max_length']})")
            score -= 0.5
        if len(tokens) > cfg["max_tokens"]:
            problems.append(f"{len(tokens)} tokens (maximo {cfg['max_tokens']})")
            score -= 0.4
        if len(tokens) < cfg["min_tokens"]:
            problems.append("demasiado corto")
            score -= 0.4
        if not problems:
            return 1.0, "Longitud y numero de tokens dentro de rango."
        return max(score, 0.0), "Problemas: " + "; ".join(problems) + "."

    def _documented(self, description: str | None, name: str) -> tuple[float, str]:
        if not description or not description.strip():
            return (
                0.0,
                (
                    "Sin descripcion. Un activo sin descripcion es invisible para el "
                    "catalogo y para los agentes de IA que consultan el esquema."
                ),
            )
        text = description.strip()
        if len(text) < 15:
            return 0.4, (
                f"Descripcion demasiado breve ({len(text)} caracteres); "
                "explica que representa y con que grano."
            )
        if text.lower().replace(" ", "_") == name.lower():
            return 0.2, "La descripcion solo repite el nombre; no agrega contexto."
        return 1.0, "Documentado."

    def _layer_structure(self, asset: DataAsset) -> tuple[float, str]:
        layer = asset.layer
        prefixes = self.policy.layer_prefixes(layer)
        if layer is Layer.UNKNOWN:
            return (
                0.5,
                (
                    "No se pudo inferir la capa del activo. Declara la label 'layer' en el "
                    "dataset o usa la convencion <dominio>_<capa> en su nombre."
                ),
            )
        if not prefixes:
            return 1.0, "La capa no exige prefijo."
        tokens = tokenize(asset.name)
        first = tokens[0] if tokens else ""
        if first in prefixes:
            return 1.0, f"Prefijo '{first}' valido para la capa {layer.value}."
        return (
            0.0,
            (
                f"La capa {layer.value} espera el patron '{self.policy.layer_pattern(layer)}'; "
                f"prefijos aceptados: {', '.join(prefixes)}."
            ),
        )

    def _dataset_structure(self, asset: DataAsset) -> tuple[float, str]:
        cfg = self.policy.raw["dataset_structure"]
        tokens = tokenize(asset.dataset_id)
        if len(tokens) >= 2 and tokens[-1] in cfg["suffixes"]:
            return 1.0, f"Dataset sigue el patron '{cfg['pattern']}'."
        return (
            0.0,
            (
                f"El dataset deberia seguir '{cfg['pattern']}' terminando en uno de: "
                f"{', '.join(cfg['suffixes'])}."
            ),
        )

    # --------------------------------------------------- categoria semantica

    def infer_category(self, column: Column) -> str:
        """Deduce la categoria semantica de una columna.

        El tipo manda cuando es concluyente (DATE, BOOL, TIMESTAMP). Cuando no lo es
        -- STRING, INT64 y NUMERIC cubren casi todo -- se buscan pistas en el nombre.
        """
        data_type = column.data_type.upper().split("(")[0].strip()
        categories = self.policy.data_categories
        for name, cfg in categories.items():
            if data_type in [t.upper() for t in cfg.get("data_types", [])]:
                return name
        tokens = set(tokenize(column.name))
        for name, cfg in categories.items():
            hints = set(cfg.get("name_hints", []))
            if hints & tokens:
                return name
        # Sin tipo concluyente ni pistas en el nombre, la categoria queda libre.
        # La tentacion es asumir que todo INT64 es una cantidad, pero eso marcaria
        # `order_hour` o `day_of_week` como infracciones y llenaria el gate de ruido.
        # Un gate ruidoso se desactiva; uno prudente se respeta.
        return "free"

    def strip_window_suffix(self, name: str) -> tuple[str, str]:
        """Separa el calificador de ventana temporal del resto del nombre.

        `net_amount_90d` -> ("net_amount", "_90d"). Devuelve el sufijo vacio cuando
        no hay ventana, para que quien llame no tenga que distinguir los dos casos.
        """
        pattern = self.policy.raw.get("window_suffix_pattern")
        if not pattern:
            return name, ""
        match = re.search(pattern, name.lower())
        if not match:
            return name, ""
        return name[: match.start()], match.group(0)

    def _data_category(self, column: Column) -> tuple[float, str]:
        if column.name in self.policy.system_columns:
            return 1.0, "Columna de sistema reconocida por la politica."
        category = self.infer_category(column)
        cfg = self.policy.data_categories[category]
        suffixes: list[str] = cfg.get("suffixes", [])
        prefixes: list[str] = cfg.get("prefixes", [])
        if not suffixes and not prefixes:
            return 1.0, "Categoria libre: no se exige sufijo."
        name, window = self.strip_window_suffix(column.name.lower())
        if any(name.endswith(s) for s in suffixes) or any(name.startswith(p) for p in prefixes):
            detail = f" (ventana '{window}' ignorada para el sufijo)" if window else ""
            return 1.0, f"Categoria '{category}': sufijo o prefijo correcto{detail}."
        expected = ", ".join(suffixes + prefixes)
        return (
            0.0,
            (
                f"Se infirio la categoria '{category}' (tipo {column.data_type}); "
                f"se espera uno de: {expected}."
            ),
        )

    # ------------------------------------------------------ recomendaciones

    def suggest_name(
        self, name: str, *, layer: Layer | None = None, column: Column | None = None
    ) -> str:
        """Construye un nombre conforme a partir de uno que no lo es.

        No pretende adivinar la intencion de negocio -- para eso esta el LLM -- pero
        resuelve mecanicamente lo mecanico: snake_case, abreviaturas y sufijo semantico.
        """
        # La ventana temporal se aparta antes de reconstruir y se vuelve a pegar al
        # final, para que `net_amt_90d` sugiera `net_amount_90d` y no
        # `net_amount_90d_amount`.
        name, window = self.strip_window_suffix(name)
        base = re.sub(r"(?<!^)(?=[A-Z])", "_", name)
        base = re.sub(r"[^a-zA-Z0-9_]+", "_", base).lower()
        tokens = [t for t in tokenize(base) if t and t not in self.policy.noise_tokens]
        tokens = [self.policy.abbreviations.get(t, t) for t in tokens]
        deduped: list[str] = []
        for token in tokens:
            if not deduped or deduped[-1] != token:
                deduped.append(token)
        tokens = deduped

        if column is not None:
            category = self.infer_category(column)
            cfg = self.policy.data_categories[category]
            suffixes = cfg.get("suffixes", [])
            prefixes = cfg.get("prefixes", [])
            joined = "_".join(tokens)
            has_affix = any(joined.endswith(s.lstrip("_")) for s in suffixes) or any(
                joined.startswith(p) for p in prefixes
            )
            if suffixes and not has_affix:
                tokens.append(suffixes[0].lstrip("_"))
            elif prefixes and not has_affix:
                tokens.insert(0, prefixes[0].rstrip("_"))
        elif layer is not None:
            prefixes = self.policy.layer_prefixes(layer)
            if prefixes and (not tokens or tokens[0] not in prefixes):
                tokens.insert(0, prefixes[0])

        return "_".join(tokens) + window

    # -------------------------------------------------------- evaluaciones

    def _score(
        self, kind: str, scores: dict[str, tuple[float, str]]
    ) -> tuple[float, list[CriterionScore]]:
        weights = self.policy.weights(kind)
        breakdown = [
            CriterionScore(criterion=name, weight=weights[name], score=value, reasoning=why)
            for name, (value, why) in scores.items()
            if name in weights
        ]
        return sum(c.weighted for c in breakdown), breakdown

    def _evaluate_relation(self, asset: DataAsset) -> Assessment:
        scores = {
            "snake_case": self._snake_case(asset.name),
            "full_words": self._full_words(asset.name),
            "layer_structure": self._layer_structure(asset),
            "no_redundancy": self._no_redundancy(asset.name),
            "readable": self._readable(asset.name),
            "documented": self._documented(asset.description, asset.name),
        }
        total, breakdown = self._score("table", scores)
        threshold = self.policy.threshold("table")
        failed = [c.criterion for c in breakdown if c.score < 1.0]
        return Assessment(
            asset_id=asset.asset_id,
            target=asset.name,
            check="naming.table",
            score=total,
            threshold=threshold,
            breakdown=breakdown,
            severity=self.policy.severity(asset.layer),
            message=(
                "Nombre de tabla conforme."
                if total >= threshold
                else "Criterios no satisfechos: " + ", ".join(failed) + "."
            ),
            recommendation=(
                None if total >= threshold else self.suggest_name(asset.name, layer=asset.layer)
            ),
            source_path=asset.source_path,
        )

    def _evaluate_column(self, asset: DataAsset, column: Column) -> Assessment:
        scores = {
            "snake_case": self._snake_case(column.name),
            "full_words": self._full_words(column.name),
            "data_category": self._data_category(column),
            "no_redundancy": self._no_redundancy(column.name, parent=asset.name),
            "readable": self._readable(column.name),
            "documented": self._documented(column.description, column.name),
        }
        total, breakdown = self._score("column", scores)
        threshold = self.policy.threshold("column")
        failed = [c.criterion for c in breakdown if c.score < 1.0]
        return Assessment(
            asset_id=asset.asset_id,
            target=f"{asset.name}.{column.name}",
            check="naming.column",
            score=total,
            threshold=threshold,
            breakdown=breakdown,
            severity=self.policy.severity(asset.layer),
            message=(
                "Nombre de columna conforme."
                if total >= threshold
                else "Criterios no satisfechos: " + ", ".join(failed) + "."
            ),
            recommendation=(
                None if total >= threshold else self.suggest_name(column.name, column=column)
            ),
            source_path=asset.source_path,
        )

    def _evaluate_dataset(self, asset: DataAsset) -> Assessment:
        scores = {
            "snake_case": self._snake_case(asset.dataset_id),
            "full_words": self._full_words(asset.dataset_id),
            "layer_structure": self._dataset_structure(asset),
            "readable": self._readable(asset.dataset_id),
            "documented": self._documented(asset.description, asset.dataset_id),
        }
        total, breakdown = self._score("dataset", scores)
        threshold = self.policy.threshold("dataset")
        failed = [c.criterion for c in breakdown if c.score < 1.0]
        return Assessment(
            asset_id=asset.asset_id,
            target=asset.dataset_id,
            check="naming.dataset",
            score=total,
            threshold=threshold,
            breakdown=breakdown,
            severity=self.policy.severity(asset.layer),
            message=(
                "Nombre de dataset conforme."
                if total >= threshold
                else "Criterios no satisfechos: " + ", ".join(failed) + "."
            ),
            source_path=asset.source_path,
        )
