# Taxonomias de policy tags: el control de acceso a nivel de columna de BigQuery.
#
# Como funciona, porque no es obvio: una columna etiquetada con un policy tag deja de
# ser legible para cualquiera que no tenga `roles/datacatalog.categoryFineGrainedReader`
# sobre ESE tag. No es que la columna vuelva nula -- la consulta falla. Eso obliga a
# que quien escribe SQL sea explicito sobre si necesita el dato personal o no, en vez
# de arrastrarlo con un SELECT *.
#
# Las taxonomias replican las categorias de `governance/faro_governance/rules/privacy.yaml`.
# Esa correspondencia es lo que permite que el gate de CI verifique el etiquetado sin
# una tabla de equivalencias aparte: el catalogo dice que tag corresponde, Terraform lo
# crea, y el output de abajo se lo entrega a Dataform.

locals {
  # policy_tag_id -> descripcion. El id coincide con el campo `policy_tag` del catalogo.
  policy_tags = {
    "person.national_id" = {
      taxonomy    = "person"
      display     = "Identificador nacional"
      description = "RUT, DNI, CPF, pasaporte. Identificador directo y no revocable."
    }
    "person.contact" = {
      taxonomy    = "person"
      display     = "Dato de contacto"
      description = "Correo electronico y telefono."
    }
    "person.name" = {
      taxonomy    = "person"
      display     = "Nombre de persona"
      description = "Nombre completo o partes de el."
    }
    "person.location" = {
      taxonomy    = "person"
      display     = "Ubicacion de persona"
      description = "Direccion, codigo postal, coordenadas."
    }
    "person.online_identifier" = {
      taxonomy    = "person"
      display     = "Identificador en linea"
      description = "IP, identificador de dispositivo, sesion, cookie."
    }
    "person.reference" = {
      taxonomy    = "person"
      display     = "Referencia a persona"
      description = "Clave que permite reconectar una fila con una persona identificada."
    }
    "person.demographic" = {
      taxonomy    = "person"
      display     = "Dato demografico"
      description = "Fecha de nacimiento, edad, genero, nacionalidad."
    }
    "person.special_category" = {
      taxonomy    = "person"
      display     = "Dato sensible"
      description = "Art. 2 g) Ley 21.719: salud, biometria, origen, creencias, vida sexual."
    }
    "financial.payment_instrument" = {
      taxonomy    = "financial"
      display     = "Medio de pago"
      description = "Numero de tarjeta, cuenta bancaria, IBAN. Alcance PCI-DSS."
    }
    "financial.compensation" = {
      taxonomy    = "financial"
      display     = "Remuneracion"
      description = "Sueldo, bonos, ingresos declarados."
    }
    "security.credential" = {
      taxonomy    = "security"
      display     = "Credencial"
      description = "Contrasenias, hashes, tokens, claves de API."
    }
    "commercial.internal" = {
      taxonomy    = "commercial"
      display     = "Dato comercial interno"
      description = "Costos, margenes, precios de compra."
    }
  }

  taxonomy_names = toset([for k, v in local.policy_tags : v.taxonomy])
}

resource "google_data_catalog_taxonomy" "this" {
  for_each = local.taxonomy_names

  project                = var.governance_project_id
  region                 = var.region
  display_name           = each.value
  description            = "Taxonomia de clasificacion: ${each.value}"
  activated_policy_types = ["FINE_GRAINED_ACCESS_CONTROL"]
}

resource "google_data_catalog_policy_tag" "this" {
  for_each = local.policy_tags

  taxonomy     = google_data_catalog_taxonomy.this[each.value.taxonomy].id
  display_name = each.value.display
  description  = each.value.description
}

# Solo el grupo de privacidad puede leer columnas de la taxonomia `person`.
# Nadie mas, incluidos los duenios del producto: ser duenio de la tabla no implica
# poder leer el dato personal que contiene, y esa separacion es el punto.
resource "google_data_catalog_taxonomy_iam_member" "person_readers" {
  project  = var.governance_project_id
  region   = var.region
  taxonomy = google_data_catalog_taxonomy.this["person"].name
  role     = "roles/datacatalog.categoryFineGrainedReader"
  member   = "group:${var.privacy_officer_group}"
}
