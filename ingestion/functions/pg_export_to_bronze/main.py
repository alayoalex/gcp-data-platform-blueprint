"""Ingesta de archivos planos hacia la capa bronze.

Datastream cubre las fuentes con CDC. Esta funcion cubre el resto, que en la practica
es la mitad del trabajo: el catalogo que un proveedor deja en un bucket cada noche, el
export de la plataforma de marketing, el CSV que alguien sube a mano una vez al mes.

Tres reglas que hacen la diferencia entre una ingesta que se puede operar y una que
produce incidentes silenciosos:

1. **El esquema se declara, no se infiere.** La autodeteccion de BigQuery funciona
   hasta el dia que un archivo llega con una columna vacia y el tipo cambia de INT64 a
   STRING. Lo que sigue es una tabla que ya no se puede consultar y un lunes perdido.

2. **Los archivos malos se apartan, no se descartan.** Un archivo rechazado va a un
   prefijo de cuarentena con el motivo al lado. Descartarlo deja un hueco en los datos
   que nadie nota hasta el cierre de mes.

3. **La ingesta es idempotente.** El mismo archivo procesado dos veces produce el mismo
   resultado. Los reintentos de Cloud Run no son hipoteticos: ocurren.

Sobre la configuracion: se lee de forma perezosa y cacheada, no en el import. Leerla en
el import hace que el modulo no se pueda importar sin el entorno completo, y eso vuelve
la funcion imposible de testear -- que es exactamente como terminan sin tests las piezas
de ingesta, que son las que mas fallan en produccion.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pg_export_to_bronze")

#: `incoming/<feed>/<YYYY-MM-DD>/archivo.csv`
OBJECT_PATTERN = re.compile(
    r"^incoming/(?P<feed>[a-z0-9_]+)/(?P<partition_date>\d{4}-\d{2}-\d{2})/[^/]+\.csv$"
)


@dataclass(frozen=True)
class Settings:
    project_id: str
    bronze_dataset: str = "commerce_bronze"
    quarantine_prefix: str = "_quarantine"
    schema_bucket: str | None = None
    source_system: str = "shop"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            project_id=os.environ.get("GCP_PROJECT_ID", ""),
            bronze_dataset=os.environ.get("BRONZE_DATASET", "commerce_bronze"),
            quarantine_prefix=os.environ.get("QUARANTINE_PREFIX", "_quarantine"),
            schema_bucket=os.environ.get("SCHEMA_BUCKET"),
            source_system=os.environ.get("SOURCE_SYSTEM", "shop"),
        )


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings.from_env()


class RejectedFile(Exception):
    """El archivo no cumple el contrato de ingesta y va a cuarentena."""


@dataclass
class FeedConfig:
    """Contrato de ingesta de un feed: que tabla alimenta y con que esquema."""

    feed: str
    target_table: str
    write_disposition: str
    schema: list[dict[str, Any]]

    @classmethod
    def load(cls, storage_client, feed: str) -> FeedConfig:
        """Lee `schemas/<feed>.json` del bucket de configuracion.

        Vive en un bucket y no en el codigo de la funcion para que agregar un feed sea
        un cambio de configuracion revisable y no un redespliegue. El archivo pasa por
        el mismo merge request que todo lo demas.
        """
        bucket_name = settings().schema_bucket
        if not bucket_name:
            raise RejectedFile("SCHEMA_BUCKET no esta configurado en el entorno.")
        blob = storage_client.bucket(bucket_name).blob(f"schemas/{feed}.json")
        if not blob.exists():
            raise RejectedFile(f"No existe contrato de ingesta para el feed '{feed}'.")
        spec = json.loads(blob.download_as_text())
        missing = [k for k in ("target_table", "schema") if k not in spec]
        if missing:
            raise RejectedFile(f"El contrato del feed '{feed}' no declara: {', '.join(missing)}.")
        return cls(
            feed=feed,
            target_table=spec["target_table"],
            write_disposition=spec.get("write_disposition", "WRITE_TRUNCATE"),
            schema=spec["schema"],
        )

    def bigquery_schema(self):
        """Traduce el contrato a `SchemaField`, agregando las columnas de auditoria."""
        from google.cloud import bigquery

        fields = [
            bigquery.SchemaField(
                name=f["name"],
                field_type=f["type"],
                mode=f.get("mode", "NULLABLE"),
                description=f.get("description"),
            )
            for f in self.schema
        ]
        return [
            *fields,
            bigquery.SchemaField(
                "ingestion_datetime", "TIMESTAMP", description="Momento de la ingesta (UTC)."
            ),
            bigquery.SchemaField(
                "source_system_name", "STRING", description="Sistema de origen del archivo."
            ),
            bigquery.SchemaField(
                "source_file_name", "STRING", description="Objeto de GCS del que proviene la fila."
            ),
        ]


def route(object_name: str) -> tuple[str, str] | None:
    """Decide si un objeto de GCS corresponde a una ingesta y a que feed y particion.

    Devuelve `None` para lo que hay que ignorar. Es una funcion pura a proposito: es la
    logica que mas se equivoca y la unica que se puede testear sin nube.
    """
    match = OBJECT_PATTERN.match(object_name)
    if match is None:
        return None
    return match.group("feed"), match.group("partition_date")


def quarantine_path(prefix: str, object_name: str, stamp: str) -> str:
    return f"{prefix}/{stamp}/{object_name}"


def handle_event(bucket_name: str, object_name: str, storage_client=None) -> dict[str, Any]:
    """Nucleo de la funcion, sin el decorador de Cloud Functions.

    Separarlo del punto de entrada permite invocarlo desde un test con un cliente de
    storage apuntando al emulador, sin construir un CloudEvent a mano.
    """
    from google.cloud import storage as gcs

    cfg = settings()
    destination = route(object_name)
    if destination is None:
        # No es un error: el bucket recibe tambien los archivos de cuarentena y los
        # esquemas. Ignorar lo que no calza evita un bucle de reintentos infinito.
        log.info("Objeto ignorado por no calzar con el patron de ingesta: %s", object_name)
        return {"status": "ignored", "object": object_name}

    feed, partition_date = destination
    client = storage_client or gcs.Client(project=cfg.project_id)

    try:
        config = FeedConfig.load(client, feed)
        job = _load(bucket_name, object_name, config, partition_date)
    except RejectedFile as exc:
        _quarantine(client, bucket_name, object_name, str(exc))
        log.warning("Archivo en cuarentena: %s (%s)", object_name, exc)
        return {"status": "quarantined", "object": object_name, "reason": str(exc)}

    log.info(
        "Cargadas %s filas de %s en %s.%s",
        job.output_rows,
        object_name,
        cfg.bronze_dataset,
        config.target_table,
    )
    return {
        "status": "loaded",
        "object": object_name,
        "table": f"{cfg.bronze_dataset}.{config.target_table}",
        "rows": job.output_rows,
    }


def handle(event) -> dict[str, Any]:
    """Punto de entrada de Cloud Functions (trigger de finalizacion de objeto en GCS)."""
    return handle_event(event.data["bucket"], event.data["name"])


try:  # pragma: no cover - solo aplica dentro del runtime de Cloud Functions
    import functions_framework

    handle = functions_framework.cloud_event(handle)
except ImportError:
    # En un test no hace falta el runtime. Importarlo como requisito duro impediria
    # probar la logica de routing, que es justamente donde estan los errores.
    pass


def _load(bucket: str, obj: str, config: FeedConfig, partition_date: str):
    from google.cloud import bigquery

    cfg = settings()
    client = bigquery.Client(project=cfg.project_id)

    # El destino es la particion del dia, no la tabla completa. Asi el reproceso de un
    # archivo reemplaza solo su dia y no borra el historial: la diferencia entre
    # "corregimos el martes" y "perdimos el trimestre".
    suffix = partition_date.replace("-", "")
    table_id = f"{cfg.project_id}.{cfg.bronze_dataset}.{config.target_table}${suffix}"

    job_config = bigquery.LoadJobConfig(
        schema=config.bigquery_schema(),
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,
        write_disposition=config.write_disposition,
        # Cero tolerancia a filas malas. Un umbral distinto de cero significa aceptar
        # que una fraccion desconocida de los datos se perdio en silencio, y esa
        # fraccion siempre resulta ser la que importaba.
        max_bad_records=0,
        # Sin esto, una columna nueva en el origen rompe la carga con un error claro.
        # Con esto, la columna entraria sin descripcion ni clasificacion, que es peor:
        # un dato sin gobernar que nadie sabe que llego.
        schema_update_options=[],
        time_partitioning=bigquery.TimePartitioning(field="ingestion_datetime"),
    )

    uri = f"gs://{bucket}/{obj}"
    load_job = client.load_table_from_uri(uri, table_id, job_config=job_config)

    try:
        load_job.result(timeout=900)
    except Exception as exc:
        raise RejectedFile(f"BigQuery rechazo la carga: {exc}") from exc

    if load_job.errors:
        raise RejectedFile(f"Errores en la carga: {load_job.errors}")

    _stamp_audit_columns(client, config, obj)
    return load_job


def _stamp_audit_columns(client, config: FeedConfig, obj: str) -> None:
    """Rellena las columnas de auditoria de las filas recien cargadas.

    El job de carga no puede calcularlas, asi que se aplican en un UPDATE acotado a la
    particion. Es el precio de tener trazabilidad fila a fila hasta el archivo de origen,
    y se paga una vez por ingesta.
    """
    from google.cloud import bigquery

    cfg = settings()
    query = f"""
    UPDATE `{cfg.project_id}.{cfg.bronze_dataset}.{config.target_table}`
    SET
      ingestion_datetime = @ingested_at,
      source_system_name = @source_system,
      source_file_name = @source_file
    WHERE ingestion_datetime IS NULL
    """
    client.query(
        query,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "ingested_at", "TIMESTAMP", datetime.now(timezone.utc)
                ),
                bigquery.ScalarQueryParameter("source_system", "STRING", cfg.source_system),
                bigquery.ScalarQueryParameter("source_file", "STRING", obj),
            ]
        ),
    ).result()


def _quarantine(client, bucket_name: str, obj: str, reason: str) -> None:
    bucket = client.bucket(bucket_name)
    source = bucket.blob(obj)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = quarantine_path(settings().quarantine_prefix, obj, stamp)

    bucket.copy_blob(source, bucket, target)
    # El motivo viaja junto al archivo. Quien revise la cuarentena manana no tiene por
    # que ir a buscar el log de la ejecucion que lo dejo ahi.
    bucket.blob(f"{target}.reason.txt").upload_from_string(
        f"rechazado_en: {stamp}\nmotivo: {reason}\norigen: gs://{bucket_name}/{obj}\n",
        content_type="text/plain",
    )
    source.delete()
