# Replica CDC de la base transaccional hacia BigQuery.
#
# Por que Datastream y no un job de extraccion propio: porque la alternativa honesta a
# un CDC gestionado no es "un script simple", es reimplementar el manejo de esquemas
# cambiantes, el backfill, el reintento tras una caida de red y el orden de los eventos.
# Ese trabajo tiene sentido cuando hay una razon concreta -- un motor que Datastream no
# soporta, una restriccion de red insalvable -- y no antes.
#
# Lo que si hay que decidir aqui, y no es automatico:
#
#  - Que tablas se replican. `include_objects` es una lista explicita, no un comodin.
#    Replicar la base entera trae las tablas de sesiones, de colas y de auditoria del
#    framework, que no aportan nada analitico y si arrastran datos personales.
#  - Que columnas NO se replican. `hashed_password` se excluye en el origen, no se
#    filtra despues: un dato que nunca sale de la base transaccional no puede filtrarse
#    desde el warehouse.

resource "google_datastream_connection_profile" "source_postgres" {
  project               = var.project_id
  location              = var.region
  display_name          = "shop-postgres"
  connection_profile_id = "shop-postgres"

  postgresql_profile {
    hostname = var.source_postgres_host
    port     = 5432
    username = var.source_postgres_user
    password = var.source_postgres_password
    database = "shop"
  }

  private_connectivity {
    private_connection = google_datastream_private_connection.vpc_peering.id
  }
}

resource "google_datastream_private_connection" "vpc_peering" {
  project               = var.project_id
  location              = var.region
  display_name          = "shop-private-connection"
  private_connection_id = "shop-private-connection"

  vpc_peering_config {
    vpc    = var.vpc_self_link
    subnet = "10.80.0.0/29"
  }
}

resource "google_datastream_connection_profile" "destination_bigquery" {
  project               = var.project_id
  location              = var.region
  display_name          = "bigquery-cdc-landing"
  connection_profile_id = "bigquery-cdc-landing"

  bigquery_profile {}
}

resource "google_datastream_stream" "shop_cdc" {
  project      = var.project_id
  location     = var.region
  stream_id    = "shop-cdc"
  display_name = "Replica CDC de la tienda"
  desired_state = "RUNNING"

  source_config {
    source_connection_profile = google_datastream_connection_profile.source_postgres.id

    postgresql_source_config {
      publication      = "datastream_publication"
      replication_slot = "datastream_slot"

      include_objects {
        postgresql_schemas {
          schema = "public"

          dynamic "postgresql_tables" {
            for_each = var.replicated_tables
            content {
              table = postgresql_tables.value
            }
          }
        }
      }

      exclude_objects {
        postgresql_schemas {
          schema = "public"
          postgresql_tables {
            table = "users"
            postgresql_columns {
              column = "hashed_password"
            }
          }
        }
      }
    }
  }

  destination_config {
    destination_connection_profile = google_datastream_connection_profile.destination_bigquery.id

    bigquery_destination_config {
      # 900s de staleness: el costo de BigQuery por aplicar los cambios baja bastante
      # al agrupar, y ningun consumidor de esta plataforma necesita latencia menor.
      # Si manana alguien la necesita, que lo pida con un caso de uso y un presupuesto.
      data_freshness = "900s"

      single_target_dataset {
        dataset_id = "${var.project_id}:datastream_shop"
      }
    }
  }

  backfill_all {}
}
