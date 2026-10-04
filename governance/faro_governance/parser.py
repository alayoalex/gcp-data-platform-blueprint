"""Adaptadores de entrada: de la herramienta al modelo de dominio.

Faro no se acopla a Dataform. Se acopla a `DataAsset`. Este modulo traduce.

Hoy hay tres fuentes:

- `parse_dataform_compilation`: la salida de `dataform compile --json`. Es la fuente
  del gate de pre-merge, porque existe antes de que la tabla exista.
- `parse_information_schema`: un export de `INFORMATION_SCHEMA.COLUMNS`. Es la fuente
  del escaneo de un warehouse que ya esta en produccion -- el punto de partida tipico
  de un cliente que llega con 400 tablas sin gobernar.
- `parse_assets_json`: una lista plana de activos, para pruebas y para integrar
  cualquier otra herramienta sin escribir un adaptador nuevo.

Limitacion honesta de la primera: la compilacion de Dataform NO conoce los tipos de
las columnas (los resuelve BigQuery al ejecutar). Por eso el parser acepta
`type_hints`, que la CLI llena desde los contratos de datos. Sin contrato, el tipo
llega como UNKNOWN y las reglas que dependen del tipo se abstienen en vez de inventar.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from faro_governance.models import AssetType, Column, DataAsset

_DATAFORM_TYPES = {
    "table": AssetType.TABLE,
    "view": AssetType.VIEW,
    "incremental": AssetType.INCREMENTAL,
    "declaration": AssetType.TABLE,
}

UNKNOWN_TYPE = "UNKNOWN"


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_dataform_compilation(
    path: str | Path,
    *,
    type_hints: dict[str, str] | None = None,
    include_declarations: bool = False,
) -> list[DataAsset]:
    payload = _read_json(path)
    hints = type_hints or {}
    assets: list[DataAsset] = []

    sections = ["tables"]
    if include_declarations:
        sections.append("declarations")

    for section in sections:
        for node in payload.get(section, []) or []:
            target = node.get("target", {})
            descriptor = node.get("actionDescriptor", {}) or {}
            project_id = target.get("database", "")
            dataset_id = target.get("schema", "")
            name = target.get("name", "")
            asset_ref = f"{project_id}.{dataset_id}.{name}"

            columns = [
                Column(
                    name=_column_name(col),
                    data_type=hints.get(f"{asset_ref}.{_column_name(col)}", UNKNOWN_TYPE),
                    description=col.get("description"),
                    policy_tag=_first_policy_tag(col),
                )
                for col in descriptor.get("columns", []) or []
            ]

            assets.append(
                DataAsset(
                    project_id=project_id,
                    dataset_id=dataset_id,
                    name=name,
                    asset_type=_DATAFORM_TYPES.get(node.get("type", "table"), AssetType.TABLE),
                    description=descriptor.get("description"),
                    columns=columns,
                    tags=node.get("tags", []) or [],
                    labels=_labels_from(node),
                    source_path=node.get("fileName"),
                )
            )
    return assets


def _column_name(col: dict[str, Any]) -> str:
    """Dataform expresa la columna como `path`, que para campos anidados trae varios
    segmentos. Se toma el ultimo: es el nombre que el analista ve y escribe."""
    path = col.get("path") or []
    if isinstance(path, list) and path:
        return str(path[-1])
    return str(col.get("name", ""))


def _first_policy_tag(col: dict[str, Any]) -> str | None:
    tags = col.get("bigqueryPolicyTags") or []
    return str(tags[0]) if tags else None


def _labels_from(node: dict[str, Any]) -> dict[str, str]:
    bq = node.get("bigquery", {}) or {}
    labels = bq.get("labels", {}) or {}
    return {str(k): str(v) for k, v in labels.items()}


def parse_information_schema(path: str | Path) -> list[DataAsset]:
    """Agrupa filas de INFORMATION_SCHEMA.COLUMNS en activos.

    Se espera el resultado de una consulta como:

        SELECT table_catalog, table_schema, table_name, column_name,
               data_type, is_nullable, description
        FROM `proyecto.region-us.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS`

    exportado a JSON con `bq query --format=json`.
    """
    rows = _read_json(path)
    grouped: dict[tuple[str, str, str], DataAsset] = {}
    for row in rows:
        key = (row["table_catalog"], row["table_schema"], row["table_name"])
        asset = grouped.get(key)
        if asset is None:
            asset = DataAsset(
                project_id=key[0],
                dataset_id=key[1],
                name=key[2],
                description=row.get("table_description"),
            )
            grouped[key] = asset
        asset.columns.append(
            Column(
                name=row["column_name"],
                data_type=row.get("data_type", UNKNOWN_TYPE),
                description=row.get("description") or row.get("column_description"),
                mode="NULLABLE" if row.get("is_nullable", "YES") == "YES" else "REQUIRED",
                policy_tag=row.get("policy_tag"),
            )
        )
    return list(grouped.values())


def parse_bigquery_api(client, project_id: str, datasets: list[str] | None = None) -> list[DataAsset]:
    """Lee los activos directamente de la API de BigQuery.

    Es la fuente preferida para el escaneo de descubrimiento, por encima de exportar
    `INFORMATION_SCHEMA`, por tres razones concretas:

    - **Permisos minimos**: basta `roles/bigquery.metadataViewer`. No hace falta poder
      ejecutar consultas, que es justo el permiso que un cliente duda en otorgar a un
      proveedor externo en la primera semana.
    - **Costo cero**: leer metadatos no factura bytes procesados.
    - **Trae las labels**, que `INFORMATION_SCHEMA.COLUMNS` no expone y que son lo que
      permite inferir la capa de forma explicita en vez de heuristica.

    `client` es un `google.cloud.bigquery.Client`. Se recibe ya construido para que
    quien llama decida el proyecto, las credenciales y el endpoint -- lo que permite
    apuntarlo a un emulador en los tests.
    """
    out: list[DataAsset] = []
    wanted = set(datasets) if datasets else None

    for dataset_item in client.list_datasets(project=project_id):
        dataset_id = dataset_item.dataset_id
        if wanted is not None and dataset_id not in wanted:
            continue
        dataset = client.get_dataset(f"{project_id}.{dataset_id}")

        for table_item in client.list_tables(dataset):
            table = client.get_table(
                f"{project_id}.{dataset_id}.{table_item.table_id}"
            )
            out.append(
                DataAsset(
                    project_id=project_id,
                    dataset_id=dataset_id,
                    name=table.table_id,
                    asset_type=_bigquery_asset_type(table),
                    description=table.description,
                    columns=[_column_from_field(f) for f in table.schema],
                    # Las labels de la tabla ganan sobre las del dataset cuando ambas
                    # declaran lo mismo: la tabla es la declaracion mas especifica.
                    labels={**(dataset.labels or {}), **(table.labels or {})},
                )
            )
    return out


def _bigquery_asset_type(table) -> AssetType:
    kind = getattr(table, "table_type", "TABLE") or "TABLE"
    return {"VIEW": AssetType.VIEW, "MATERIALIZED_VIEW": AssetType.VIEW}.get(kind, AssetType.TABLE)


def _column_from_field(field) -> Column:
    return Column(
        name=field.name,
        data_type=field.field_type or UNKNOWN_TYPE,
        description=field.description,
        mode=field.mode or "NULLABLE",
        policy_tag=_policy_tag_from_field(field),
    )


def _policy_tag_from_field(field) -> str | None:
    tags = getattr(field, "policy_tags", None)
    names = getattr(tags, "names", None) if tags else None
    return str(names[0]) if names else None


def parse_assets_json(path: str | Path) -> list[DataAsset]:
    payload = _read_json(path)
    items = payload if isinstance(payload, list) else payload.get("assets", [])
    return [_asset_from_dict(item) for item in items]


def _asset_from_dict(item: dict[str, Any]) -> DataAsset:
    return DataAsset(
        project_id=item.get("project_id", ""),
        dataset_id=item.get("dataset_id", ""),
        name=item.get("name", ""),
        asset_type=AssetType(item.get("asset_type", "TABLE")),
        description=item.get("description"),
        columns=[
            Column(
                name=c["name"],
                data_type=c.get("data_type", UNKNOWN_TYPE),
                description=c.get("description"),
                mode=c.get("mode", "NULLABLE"),
                policy_tag=c.get("policy_tag"),
            )
            for c in item.get("columns", [])
        ],
        labels=item.get("labels", {}),
        tags=item.get("tags", []),
        source_path=item.get("source_path"),
    )


def type_hints_from_contracts(contracts: Iterable[Any]) -> dict[str, str]:
    """Construye el mapa `proyecto.dataset.tabla.columna -> TIPO` desde los contratos."""
    hints: dict[str, str] = {}
    for contract in contracts:
        ref = contract.asset_ref
        if not ref:
            continue
        for col in contract.columns:
            name, data_type = col.get("name"), col.get("type")
            if name and data_type:
                hints[f"{ref}.{name}"] = str(data_type).upper()
    return hints
