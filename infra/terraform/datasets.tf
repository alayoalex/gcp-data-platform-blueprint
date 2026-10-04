# Los cuatro datasets del producto, uno por capa.
#
# Separar por capa y no por equipo o por tema es lo que hace posible que el control de
# acceso sea simple: "los analistas leen gold" es una regla que cabe en una frase y se
# audita de un vistazo. Con datasets por tema, cada nuevo tema abre la pregunta de
# quien puede verlo, y la respuesta termina siendo "todos", que es como se llega a un
# warehouse donde cualquiera lee la tabla de clientes.

locals {
  layers = {
    bronze = {
      description = "Aterrizaje fiel del sistema de origen. Sin transformar. Acceso restringido al service account del producto."
      # Bronze es una copia; si se pierde, se reconstruye desde el origen. No amerita
      # el costo de retenerlo indefinidamente.
      table_expiration_days = 90
      readers               = []
    }
    silver = {
      description = "Modelo conformado: nombres corregidos, tipos normalizados, personas seudonimizadas."
      table_expiration_days = 0
      readers               = [var.owner_group]
    }
    gold = {
      description = "Productos de datos listos para consumo. Cada tabla tiene contrato, duenio y SLO."
      table_expiration_days = 0
      readers               = [var.owner_group, var.analyst_group]
    }
    feature = {
      description = "Tablas de features para entrenamiento e inferencia de modelos."
      table_expiration_days = 0
      readers               = [var.owner_group, var.analyst_group]
    }
    work = {
      description = "Area de trabajo de Dataform: tablas intermedias y resultados de aserciones. No es un producto."
      table_expiration_days = 30
      readers               = [var.owner_group]
    }
  }
}

module "layer" {
  source   = "./modules/data_product"
  for_each = local.layers

  project_id            = var.project_id
  region                = var.region
  dataset_id            = "${var.data_domain}_${each.key}"
  description           = each.value.description
  layer                 = each.key
  product_name          = var.product_name
  environment           = var.environment
  owner_service_account = google_service_account.product.email
  reader_groups         = each.value.readers
  table_expiration_days = each.value.table_expiration_days
  deletion_protection   = var.deletion_protection
}

resource "google_service_account" "product" {
  project      = var.project_id
  account_id   = "${var.product_name}-sa"
  display_name = "Service account de ejecucion del producto ${var.product_name}"
  description  = "Ejecuta Dataform y escribe en todos los datasets del producto."
}

# El SA del producto lee los datos replicados por Datastream, pero no los administra.
resource "google_bigquery_dataset_iam_member" "cdc_reader" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.cdc_landing.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.product.email}"
}

resource "google_bigquery_dataset" "cdc_landing" {
  project       = var.project_id
  dataset_id    = "datastream_shop"
  location      = var.region
  description   = "Destino de la replica CDC de la base transaccional de la tienda. Lo escribe Datastream; nadie mas."
  friendly_name = "CDC shop"

  labels = {
    layer       = "landing"
    product     = var.product_name
    environment = var.environment
    managed_by  = "datastream"
  }

  delete_contents_on_destroy = false
}
