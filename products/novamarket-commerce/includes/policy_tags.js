/**
 * Mapa de policy tags de BigQuery.
 *
 * Los ids son generados por Terraform (`infra/terraform/taxonomies.tf`) y se
 * publican como outputs. En un repositorio real este archivo lo escribe el
 * pipeline de infraestructura, no una persona: un id copiado a mano que apunta a
 * un tag borrado hace fallar el despliegue de toda la capa.
 *
 * La clave de cada tag coincide con el `policy_tag` del catalogo de
 * `faro_governance/rules/privacy.yaml`. Esa coincidencia es lo que permite que el
 * gate verifique el etiquetado sin una tabla de equivalencias aparte.
 */

const { projectId } = require("includes/constants.js");

const taxonomyProject = dataform.projectConfig.vars.policy_tag_project;
const location = "us-central1";

function tag(taxonomyId, policyTagId) {
  return `projects/${taxonomyProject}/locations/${location}/taxonomies/${taxonomyId}/policyTags/${policyTagId}`;
}

// Ids de ejemplo. Reemplazar por los outputs de Terraform.
const taxonomies = {
  person: "7100000000000000001",
  financial: "7100000000000000002",
  security: "7100000000000000003",
  commercial: "7100000000000000004",
};

const policyTags = {
  person: {
    national_id: tag(taxonomies.person, "8100000000000000011"),
    contact: tag(taxonomies.person, "8100000000000000012"),
    name: tag(taxonomies.person, "8100000000000000013"),
    location: tag(taxonomies.person, "8100000000000000014"),
    online_identifier: tag(taxonomies.person, "8100000000000000015"),
    reference: tag(taxonomies.person, "8100000000000000016"),
    demographic: tag(taxonomies.person, "8100000000000000017"),
    special_category: tag(taxonomies.person, "8100000000000000018"),
  },
  financial: {
    payment_instrument: tag(taxonomies.financial, "8100000000000000021"),
    compensation: tag(taxonomies.financial, "8100000000000000022"),
  },
  security: {
    credential: tag(taxonomies.security, "8100000000000000031"),
  },
  commercial: {
    internal: tag(taxonomies.commercial, "8100000000000000041"),
  },
};

module.exports = { policyTags, taxonomies };
