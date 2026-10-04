/**
 * Utilidades de transformacion compartidas.
 *
 * Todo lo que aparece en mas de una definicion vive aqui. El criterio no es evitar
 * teclear: es que la regla quede escrita UNA vez. Si manana se decide que la
 * seudonimizacion cambia de SHA256 a HMAC, se cambia en una funcion y no en las
 * dieciocho consultas que la aplicaban a mano, de las cuales dos se habrian olvidado.
 */

const { pseudonymSaltRef, businessTimezone } = require("includes/constants.js");

/**
 * Seudonimiza un identificador de persona.
 *
 * SHA256 sobre el identificador concatenado con una sal secreta, en hexadecimal.
 * Propiedades que importan:
 *   - Deterministico: el mismo user_id da siempre la misma clave, asi que los joins
 *     funcionan y el historial se mantiene entre corridas.
 *   - No reversible sin la sal: sin ella, un atacante con la tabla de hashes no puede
 *     reconstruir el identificador por fuerza bruta sobre el espacio de enteros.
 *   - Rotable: si la sal se compromete, se rota y todas las claves cambian. Es un
 *     evento caro (hay que reprocesar), pero es posible, y eso es lo que distingue
 *     una seudonimizacion de un hash simple.
 *
 * @param {string} expression columna o expresion SQL a seudonimizar
 */
function pseudonymize(expression) {
  return `TO_HEX(SHA256(CONCAT(CAST(${expression} AS STRING), ${pseudonymSaltRef})))`;
}

/**
 * Convierte un TIMESTAMP UTC al dia calendario del negocio.
 *
 * Parece trivial y es la fuente mas comun de discrepancias entre un reporte y el
 * sistema transaccional: una venta de las 21:30 en Santiago cae al dia siguiente si
 * se agrupa en UTC. La zona horaria del negocio se declara una vez en
 * workflow_settings.yaml y se aplica siempre a traves de esta funcion.
 */
function businessDate(expression) {
  return `DATE(${expression}, '${businessTimezone}')`;
}

/**
 * Monto neto de una linea de detalle, redondeado a la unidad monetaria.
 */
function lineNetAmount(quantityExpr, unitPriceExpr) {
  return `ROUND(${quantityExpr} * ${unitPriceExpr}, 0)`;
}

module.exports = { pseudonymize, businessDate, lineNetAmount };
