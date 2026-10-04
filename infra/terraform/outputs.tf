output "product_service_account" {
  value       = google_service_account.product.email
  description = "SA que debe configurarse en workflow_settings.yaml del repositorio Dataform."
}

output "datasets" {
  value       = { for k, m in module.layer : k => m.dataset_id }
  description = "Dataset creado por capa."
}

# Este output es el que cierra el circuito entre infraestructura y transformacion.
#
# `products/novamarket-commerce/includes/policy_tags.js` necesita los ids reales de los
# policy tags. Escribirlos a mano funciona hasta que alguien recrea una taxonomia y
# todos los ids cambian: a partir de ahi cada despliegue de la capa silver falla con un
# error que no menciona policy tags por ninguna parte.
#
# El pipeline de infraestructura ejecuta:
#   terraform output -json policy_tag_ids > ../../products/novamarket-commerce/includes/policy_tags.generated.json
# y `policy_tags.js` lee ese archivo.
output "policy_tag_ids" {
  value = {
    for key, tag in google_data_catalog_policy_tag.this : key => tag.name
  }
  description = "policy_tag del catalogo de privacidad -> nombre completo del recurso en GCP."
}

output "taxonomy_ids" {
  value = { for k, t in google_data_catalog_taxonomy.this : k => t.id }
}
