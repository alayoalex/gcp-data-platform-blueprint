# Operación

Lo que hay que sostener una vez que la plataforma está en producción. Es la parte que
menos se documenta en los blueprints y la que decide si el trabajo sobrevive al segundo
año.

## Frescura y SLOs

El contrato promete frescura; [`assert_gold_freshness`](../products/novamarket-commerce/definitions/assertions/assert_gold_freshness.sqlx)
la verifica en cada ejecución. Esa aserción es lo que convierte el contrato en algo
exigible.

Hay una deuda conocida en el diseño actual: los umbrales de la aserción replican
`spec.freshness.max_delay` de cada contrato, y duplicar un valor en dos archivos termina
mal. El paso siguiente natural es generar la aserción desde los contratos en tiempo de
compilación:

```bash
# Idea de implementación: un script que lee los contratos y emite el .sqlx.
faro generate freshness-assertion \
  --contracts products/novamarket-commerce/contracts \
  --out products/novamarket-commerce/definitions/assertions/assert_gold_freshness.sqlx
```

Mientras tanto, el gate verifica que ambos números coincidan.

### Alertas

Una regla que ahorra mucho ruido: **alertar sobre incumplimiento del contrato, no sobre
fallos de ejecución.** Un job que falla y reintenta con éxito no es un incidente. Un
producto que quedó fuera de su ventana de frescura sí lo es, haya fallado o no.

```yaml
# Cloud Monitoring, sobre el resultado de las aserciones de Dataform.
alertPolicy:
  displayName: "Producto de datos fuera de contrato"
  conditions:
    - displayName: "Aserción de frescura falló"
      conditionThreshold:
        filter: |
          resource.type="dataform.googleapis.com/Repository"
          metric.type="logging.googleapis.com/user/dataform_assertion_failed"
        comparison: COMPARISON_GT
        thresholdValue: 0
        duration: 0s
  notificationChannels:
    - "projects/novamarket-data-prod-c3d4/notificationChannels/oncall-datos"
```

## Costos

### Dónde se va el dinero

En una plataforma de este tamaño, por orden:

1. **Consultas ad hoc sobre tablas sin particionar.** Un `SELECT *` sobre una tabla de
   500 GB cuesta lo mismo cada vez que alguien lo ejecuta.
2. **Tablas de features con muchos cortes.** El producto cartesiano cliente × fecha
   crece rápido. Cortes semanales en lugar de diarios reducen el volumen por siete y
   casi nunca empeoran el modelo.
3. **Datastream.** Domina el costo fijo. La primera palanca cuando el presupuesto
   aprieta: para fuentes que toleran latencia diaria, un export batch cuesta una
   fracción.
4. **Reprocesos completos.** Un `dataform run --full-refresh` sobre todo el proyecto
   reconstruye la historia entera.

### Qué mirar cada mes

```sql
-- Costo por producto de datos, usando las labels que el módulo de Terraform garantiza.
-- Esta consulta es la razón por la que las labels no son opcionales.
SELECT
  labels.value AS product_name,
  ROUND(SUM(cost), 2) AS cost_usd,
  ROUND(SUM(usage.amount) / POW(1024, 4), 2) AS tb_processed
FROM `novamarket-billing.billing_export.gcp_billing_export_v1_XXXXXX`,
UNNEST(labels) AS labels
WHERE labels.key = 'product'
  AND service.description = 'BigQuery'
  AND DATE(usage_start_time) >= DATE_TRUNC(CURRENT_DATE(), MONTH)
GROUP BY product_name
ORDER BY cost_usd DESC
```

```sql
-- Las diez consultas más caras del mes, con su autor.
-- La conversación que sigue casi siempre es productiva: la mayoría son consultas
-- que alguien dejó en un dashboard con refresco automático y olvidó.
SELECT
  user_email,
  ROUND(SUM(total_bytes_billed) / POW(1024, 4), 2) AS tb_billed,
  COUNT(*) AS job_quantity,
  ANY_VALUE(query) AS query_sample
FROM `region-us`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
WHERE creation_time >= TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), MONTH)
  AND job_type = 'QUERY'
GROUP BY user_email
ORDER BY tb_billed DESC
LIMIT 10
```

## Linaje y cambios que rompen

Dataform conoce el grafo dentro del repositorio. Lo que no conoce es **quién consume las
tablas desde afuera**: un dashboard de Looker, un notebook, el pipeline de otro equipo.

Ese es el consumo que rompe sin aviso. Se reconstruye desde el historial de jobs:

```sql
-- Mapa productor → consumidor externo, derivado de lo que efectivamente ocurrió.
-- No depende de que nadie mantenga un registro.
WITH consumption AS (
  SELECT
    ref.dataset_id,
    ref.table_id,
    j.user_email AS consumer,
    COUNT(*) AS job_quantity,
    MAX(j.creation_time) AS last_seen_datetime
  FROM `region-us`.INFORMATION_SCHEMA.JOBS_BY_PROJECT AS j,
  UNNEST(j.referenced_tables) AS ref
  WHERE j.creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
    AND j.job_type = 'QUERY'
    AND j.state = 'DONE'
    -- Se excluye el SA del propio producto: sus lecturas son el pipeline, no consumo.
    AND j.user_email != 'novamarket-commerce-sa@novamarket-data-prod-c3d4.iam.gserviceaccount.com'
  GROUP BY 1, 2, 3
)
SELECT * FROM consumption
WHERE dataset_id IN ('commerce_gold', 'commerce_feature')
ORDER BY job_quantity DESC
```

Correrlo cada noche y publicar el resultado en una tabla convierte el gate de contratos
en algo más útil: cuando alguien intenta eliminar una columna, el mensaje puede nombrar
no sólo a los consumidores declarados en el contrato sino a los que realmente la usaron
la semana pasada.

Es el patrón que usan las plataformas grandes para gatear cambios entre equipos, y es
implementable en una tarde con un Cloud Run Job y esta consulta.

## Solicitudes de titulares de datos

La Ley 21.719 da a las personas derecho de acceso, rectificación, supresión y
oposición, con plazos acotados. La bóveda de identidad es lo que hace que responder sea
una consulta y no un proyecto.

**Acceso** —qué datos tienen de mí:

```sql
SELECT * FROM `commerce_silver.mst_customer_identity`
WHERE customer_email = @email;
-- Y con el customer_key resultante, las tablas analíticas que lo referencian.
```

**Supresión** —bórrenme:

```sql
-- 1. Se elimina de la bóveda. El seudónimo deja de ser reconectable con la persona.
DELETE FROM `commerce_silver.mst_customer_identity` WHERE customer_key = @key;

-- 2. Se registra la solicitud para que el pipeline no lo reingrese desde bronze.
INSERT INTO `commerce_silver.mst_suppression_request` (customer_key, requested_date)
VALUES (@key, CURRENT_DATE());
```

El segundo paso es el que se olvida y el que hace fallar el cumplimiento: sin él, la
próxima corrida del pipeline reconstruye la fila desde bronze y la persona reaparece.

Los agregados históricos sobreviven, y eso es correcto: un total de ventas de marzo no
es un dato personal una vez que nadie puede reconectarlo con quien compró.

## Rotación de la sal de seudonimización

Es el procedimiento más caro de la plataforma y hay que tenerlo escrito antes de
necesitarlo. Se ejecuta si la sal se compromete:

1. Generar la sal nueva en Secret Manager, **conservando la anterior**.
2. Reprocesar silver con la sal nueva en una tabla paralela.
3. Construir el mapa `clave_antigua → clave_nueva` usando la bóveda como puente.
4. Reprocesar gold y feature.
5. Cambiar las referencias y eliminar las tablas antiguas.
6. Recién ahí, retirar la sal anterior.

Toma días, no horas. Es el costo de que la seudonimización sea reversible sólo con la
sal —que es justamente la propiedad que la hace valer.

## Rutina mensual

| Frecuencia | Qué | Por qué |
|---|---|---|
| Diaria | Aserciones de frescura y de PII | Es el control activo |
| Semanal | Revisar la cuarentena de ingesta | Un archivo rechazado es un hueco en los datos |
| Mensual | Costo por producto, top 10 de consultas | Antes de la factura, no después |
| Mensual | Tablas sin consumo en 90 días | Candidatas a deprecar |
| Trimestral | Revisión de accesos a `privacy_officer_group` | El grupo crece solo si nadie lo mira |
| Trimestral | Recalibrar `privacy.yaml` con los falsos positivos observados | Mantiene la confianza en el gate |
| Anual | Revisar bases de licitud de los contratos | Los propósitos cambian |

La penúltima es la que más impacto tiene y la que siempre se posterga. Cada falso
positivo que un equipo reporta y que no se corrige es un paso hacia que dejen de
reportarlos.

---

Anterior: [Fase 6 · Consumo: ML y agentes](08-fase-6-consumo-ml-y-agentes.md) ·
Volver al [índice](../README.md)
