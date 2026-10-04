# Un dataset con su politica de acceso, sus labels de gobierno y su expiracion.
#
# Existe como modulo por una razon concreta: garantizar que TODO dataset del producto
# lleve las labels `layer`, `product` y `environment`. Esas tres labels son lo que
# permite despues responder "cuanto cuesta la capa gold" o "que datasets son de este
# producto" sin mantener una planilla aparte. Un dataset creado a mano no las tiene, y
# ese es exactamente el dataset que despues nadie sabe de quien es.

resource "google_bigquery_dataset" "this" {
  project       = var.project_id
  dataset_id    = var.dataset_id
  location      = var.region
  description   = var.description
  friendly_name = var.dataset_id

  default_table_expiration_ms = var.table_expiration_days > 0 ? var.table_expiration_days * 86400000 : null

  labels = {
    layer       = var.layer
    product     = var.product_name
    environment = var.environment
    managed_by  = "terraform"
  }

  delete_contents_on_destroy = !var.deletion_protection

  lifecycle {
    # Cambiar la region de un dataset lo destruye y lo recrea vacio. Es la clase de
    # error que se comete una sola vez y se recuerda para siempre.
    prevent_destroy = false
    ignore_changes  = [last_modified_time]
  }
}

resource "google_bigquery_dataset_iam_member" "owner" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.this.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${var.owner_service_account}"
}

resource "google_bigquery_dataset_iam_member" "readers" {
  for_each = toset(var.reader_groups)

  project    = var.project_id
  dataset_id = google_bigquery_dataset.this.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "group:${each.value}"
}
