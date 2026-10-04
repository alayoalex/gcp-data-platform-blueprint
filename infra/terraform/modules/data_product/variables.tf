variable "project_id" { type = string }
variable "region" { type = string }
variable "dataset_id" { type = string }
variable "description" { type = string }
variable "layer" { type = string }
variable "product_name" { type = string }
variable "environment" { type = string }

variable "owner_service_account" {
  type        = string
  description = "SA que ejecuta las transformaciones. Unico principal con dataEditor."
}

variable "reader_groups" {
  type        = list(string)
  default     = []
  description = "Grupos con lectura sobre el dataset. Vacio en bronze, a proposito."
}

variable "table_expiration_days" {
  type        = number
  default     = 0
  description = "Expiracion por defecto de las tablas del dataset. 0 = sin expiracion."
}

variable "deletion_protection" {
  type    = bool
  default = true
}
