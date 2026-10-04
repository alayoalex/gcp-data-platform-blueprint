/**
 * Constantes derivadas del entorno de compilacion.
 *
 * Regla de la casa: ninguna definicion .sqlx escribe un project id ni un nombre de
 * dataset literal. Todo pasa por aqui. El costo de romper esta regla no se ve el
 * primer dia; se ve el dia que hay que promover a produccion.
 */

const vars = dataform.projectConfig.vars;

const environment = vars.environment;
const projectId = vars.project_id;
const sourceSystem = vars.source_system;
const businessTimezone = vars.business_timezone;

// Datasets del producto, por capa.
const datasets = {
  bronze: "commerce_bronze",
  silver: "commerce_silver",
  gold: "commerce_gold",
  feature: "commerce_feature",
  work: "commerce_work",
};

// Dataset donde Datastream deja la replica CDC de la base transaccional.
const cdcDataset = "datastream_shop";

// Sal de seudonimizacion. En dev es un literal; en prod se resuelve desde Secret
// Manager mediante una funcion remota. El hash debe ser estable entre corridas
// (si cambia, se pierde la trazabilidad del cliente) y secreto (si se filtra,
// el seudonimo es reversible por fuerza bruta sobre un espacio pequenio de correos).
const pseudonymSaltRef = environment === "prod"
  ? "`" + projectId + "`.commerce_udf.pseudonym_salt()"
  : "'dev-salt-no-secreta'";

module.exports = {
  environment,
  projectId,
  sourceSystem,
  businessTimezone,
  datasets,
  cdcDataset,
  pseudonymSaltRef,
};
