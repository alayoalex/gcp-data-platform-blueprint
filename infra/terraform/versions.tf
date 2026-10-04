terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # El estado vive en GCS con versionado y bloqueo. Nunca en el disco de quien
  # aplica: el dia que dos personas apliquen a la vez, el estado local pierde
  # silenciosamente los recursos que la otra creo.
  backend "gcs" {
    bucket = "novamarket-terraform-state"
    prefix = "data-platform"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
