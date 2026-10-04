variable "project_id" {
  type        = string
  description = "Proyecto GCP donde vive el warehouse de este entorno."
}

variable "governance_project_id" {
  type        = string
  description = <<-EOT
    Proyecto donde viven las taxonomias de policy tags.

    Es un proyecto aparte a proposito. Las taxonomias son transversales a todos los
    productos de datos y a todos los entornos: si vivieran dentro del proyecto de
    datos, cada entorno tendria su propio conjunto de tags con ids distintos, y una
    tabla promovida de dev a prod perderia su etiquetado en el camino.
  EOT
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "Region de los datasets y de los servicios de ingesta."
}

variable "environment" {
  type        = string
  description = "Entorno: dev, intg o prod."

  validation {
    condition     = contains(["dev", "intg", "prod"], var.environment)
    error_message = "environment debe ser dev, intg o prod."
  }
}

variable "product_name" {
  type        = string
  default     = "novamarket-commerce"
  description = "Nombre del producto de datos. Deriva los nombres de SA, buckets y datasets."
}

variable "data_domain" {
  type        = string
  default     = "commerce"
  description = "Dominio de negocio. Prefijo de todos los datasets del producto."
}

variable "owner_group" {
  type        = string
  description = "Grupo de Google duenio del producto. Recibe dataOwner sobre sus datasets."
}

variable "analyst_group" {
  type        = string
  description = "Grupo de analistas. Recibe lectura sobre gold y feature, nunca sobre bronze."
}

variable "privacy_officer_group" {
  type        = string
  description = <<-EOT
    Grupo que puede leer columnas etiquetadas como datos personales.

    Es el unico grupo con fineGrainedReader sobre la taxonomia `person`. Mantenerlo
    pequenio es el control; el policy tag solo hace que el control sea exigible.
  EOT
}

variable "deletion_protection" {
  type        = bool
  default     = true
  description = "Protege los datasets de un destroy accidental. Se desactiva solo en dev."
}
