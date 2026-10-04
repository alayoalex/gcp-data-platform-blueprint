"""Clasificador LLM opcional (Vertex AI / Gemini).

Por que es opcional y por que entra al final:

El catalogo de patrones de `rules/privacy.yaml` resuelve las columnas que se llaman
como se espera. No resuelve `campo_1`, `dato_cliente_old`, ni `obs` -- que es
exactamente lo que abunda en un warehouse heredado. Ahi el LLM aporta, porque lee la
descripcion, el nombre de la tabla y el contexto del dataset.

Dos decisiones de disenio que conviene conservar al portar esto a produccion:

1. **El LLM clasifica, no decide.** Devuelve una `Classification` con confianza. La
   politica de bloqueo sigue viviendo en `privacy.py`. Un LLM en el camino critico de
   un gate de CI lo vuelve no determinista y caro.
2. **Solo ve lo desconocido.** `PrivacyClassifier` le pasa unicamente las columnas que
   las reglas no reconocieron. En un esquema maduro eso es una fraccion pequenia.

Nunca se le envian datos: solo metadatos de esquema (nombre, tipo, descripcion).
Mandar filas de ejemplo a un modelo mejora la precision y es exactamente lo que no se
puede hacer cuando el objetivo del ejercicio es proteger esos datos.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from faro_governance.models import Column, DataAsset, Sensitivity
from faro_governance.privacy import Classification, PrivacyCatalog

PROMPT = """\
Eres un clasificador de privacidad de datos. Recibes el esquema de una tabla de un
data warehouse y debes indicar, para cada columna listada, si contiene datos personales.

NO recibes datos reales, solo metadatos. No inventes contenido.

Categorias validas (usa exactamente estos identificadores):
{categories}

Si una columna no contiene datos personales, responde con category=null.

Contexto de la tabla:
  dataset: {dataset}
  tabla: {table}
  descripcion: {description}

Columnas a clasificar:
{columns}

Responde SOLO con un array JSON, un objeto por columna:
[{{"column_name": "...", "category": "..." | null, "confidence": 0.0-1.0, "reasoning": "..."}}]
"""


@dataclass
class VertexClassifier:
    """Implementa el protocolo `LlmClassifier` sobre Vertex AI.

    Requiere `google-cloud-aiplatform`. Se instala con el extra `llm`:
        pip install -e ".[llm]"
    """

    project_id: str = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    location: str = os.environ.get("VERTEX_LOCATION", "us-central1")
    model: str = os.environ.get("FARO_LLM_MODEL", "gemini-2.5-flash")
    catalog: PrivacyCatalog | None = None

    def __post_init__(self) -> None:
        self.catalog = self.catalog or PrivacyCatalog.load()

    def classify(self, asset: DataAsset, columns: list[Column]) -> list[Classification]:
        if not columns:
            return []
        raw = self._invoke(self._build_prompt(asset, columns))
        return self._parse(raw, columns)

    # ------------------------------------------------------------------ impl

    def _build_prompt(self, asset: DataAsset, columns: list[Column]) -> str:
        assert self.catalog is not None
        categories = "\n".join(
            f"  - {c['id']}: {c['label']} ({c['sensitivity']})" for c in self.catalog.raw["categories"]
        )
        column_lines = "\n".join(
            f"  - {c.name} ({c.data_type}): {c.description or 'sin descripcion'}" for c in columns
        )
        return PROMPT.format(
            categories=categories,
            dataset=asset.dataset_id,
            table=asset.name,
            description=asset.description or "sin descripcion",
            columns=column_lines,
        )

    def _invoke(self, prompt: str) -> str:
        import vertexai  # type: ignore
        from vertexai.generative_models import GenerationConfig, GenerativeModel  # type: ignore

        vertexai.init(project=self.project_id, location=self.location)
        model = GenerativeModel(self.model)
        response = model.generate_content(
            prompt,
            generation_config=GenerationConfig(
                temperature=0.0,  # la clasificacion no debe variar entre corridas
                response_mime_type="application/json",
            ),
        )
        return response.text

    def _parse(self, raw: str, columns: list[Column]) -> list[Classification]:
        assert self.catalog is not None
        by_id = {c["id"]: c for c in self.catalog.raw["categories"]}
        known = {c.name for c in columns}
        out: list[Classification] = []
        for item in json.loads(raw):
            name = item.get("column_name")
            if name not in known:
                continue  # el modelo invento una columna: se descarta
            entry = by_id.get(item.get("category") or "")
            if entry is None:
                continue
            out.append(
                Classification(
                    column_name=name,
                    category=entry["id"],
                    label=entry["label"],
                    sensitivity=Sensitivity(entry["sensitivity"]),
                    policy_tag=entry["policy_tag"],
                    confidence=float(item.get("confidence", 0.5)),
                    special_category=entry.get("special_category", False),
                    rationale=str(item.get("reasoning", "")),
                    source="llm",
                )
            )
        return out
