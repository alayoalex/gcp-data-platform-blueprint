/**
 * Fabrica de tablas bronze.
 *
 * Toda tabla bronze hace exactamente lo mismo: tomar el stream CDC de Datastream,
 * quedarse con la ultima version de cada fila, y agregar las columnas de auditoria
 * de la plataforma. Escribir eso 30 veces a mano garantiza que 30 tablas terminen
 * ligeramente distintas entre si. Se declara una vez.
 *
 * Bronze NO transforma: no renombra columnas, no castea tipos, no filtra filas.
 * Si algo llega mal del origen, bronze lo refleja mal, y esa es la unica forma de
 * poder reconstruir silver cuando se descubre un error de negocio seis meses despues.
 */

const { cdcDataset, datasets, sourceSystem } = require("includes/constants.js");

/**
 * @param {string} name        nombre del activo bronze, ej. "raw_shop_order"
 * @param {string} sourceTable tabla en el dataset CDC, ej. "orders"
 * @param {string} primaryKey  columna llave en el origen
 * @param {string} description descripcion del activo para el catalogo
 * @param {object} columns     mapa columna -> descripcion
 * @param {string[]} tags      etiquetas de planificacion, ej. ["hourly"]
 */
function cdcLanding({ name, sourceTable, primaryKey, description, columns, tags = [] }) {
  publish(name, {
    type: "incremental",
    schema: datasets.bronze,
    description: description,
    columns: Object.assign({}, columns, {
      ingestion_datetime: "Momento en que la plataforma ingesto la fila (UTC).",
      source_system_name: "Sistema de origen que produjo la fila.",
      is_deleted_flag: "Verdadero si la fila fue eliminada en el origen (borrado logico del CDC).",
    }),
    bigquery: {
      partitionBy: "DATE(ingestion_datetime)",
      clusterBy: [primaryKey],
      labels: { layer: "bronze", product: "novamarket-commerce" },
    },
    tags: ["bronze"].concat(tags),
    assertions: { uniqueKey: [primaryKey] },
  }).query(
    (ctx) => `
SELECT
  src.* EXCEPT (datastream_metadata),
  CURRENT_TIMESTAMP() AS ingestion_datetime,
  '${sourceSystem}' AS source_system_name,
  COALESCE(src.datastream_metadata.is_deleted, FALSE) AS is_deleted_flag
FROM ${ctx.ref(cdcDataset, sourceTable)} AS src
${ctx.when(
  ctx.incremental(),
  `WHERE src.datastream_metadata.source_timestamp > (
     SELECT COALESCE(MAX(ingestion_datetime), TIMESTAMP('1970-01-01'))
     FROM ${ctx.self()}
   )`
)}
-- El stream trae todas las versiones de cada fila. Bronze conserva solo la ultima
-- conocida: reconstruir el historial completo es trabajo de una tabla SCD en silver,
-- no de la capa de aterrizaje.
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY src.${primaryKey}
  ORDER BY src.datastream_metadata.source_timestamp DESC
) = 1
`
  );
}

module.exports = { cdcLanding };
