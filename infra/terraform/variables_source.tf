# Variables de la conexion al sistema de origen.
#
# La contrasenia entra como variable sensible y se resuelve desde Secret Manager en el
# pipeline, no se escribe en terraform.tfvars. Terraform guarda el valor en el estado
# de todas formas -- por eso el bucket de estado tiene acceso restringido y versionado.
# Quien tenga lectura sobre ese bucket tiene la contrasenia de la base de produccion:
# conviene saberlo explicitamente en vez de descubrirlo en una auditoria.

variable "source_postgres_host" {
  type        = string
  description = "Host de la base transaccional de la tienda."
}

variable "source_postgres_user" {
  type        = string
  default     = "datastream"
  description = "Usuario de replicacion. Solo necesita REPLICATION y SELECT sobre las tablas replicadas."
}

variable "source_postgres_password" {
  type        = string
  sensitive   = true
  description = "Contrasenia del usuario de replicacion. Se inyecta desde Secret Manager."
}

variable "vpc_self_link" {
  type        = string
  description = "VPC desde la que Datastream alcanza la base de origen."
}

variable "replicated_tables" {
  type        = list(string)
  description = <<-EOT
    Tablas del origen que se replican. Lista explicita, nunca un comodin.

    Cada tabla que se agrega aqui es una decision: trae datos que alguien tendra que
    clasificar, gobernar y, eventualmente, borrar cuando un titular lo solicite.
  EOT
}
