# Fase 0 · Descubrimiento

**Duración típica:** 2 a 3 semanas
**Entregable:** un informe con cifras sobre el warehouse actual y una decisión sobre
si hay proyecto.

## Por qué existe esta fase

La tentación es saltarla. El cliente ya dijo cuál es su problema, el equipo técnico ya
tiene una opinión, y el diagnóstico se siente como tiempo que no construye nada.

Vale la pena resistirla por una razón práctica: **casi siempre el problema declarado
no es el problema principal.** El cliente llega diciendo "necesitamos un catálogo de
datos" y lo que tiene es una base transaccional consultada directamente por catorce
personas con credenciales compartidas. Un catálogo sobre eso no arregla nada.

Y hay una razón comercial: el diagnóstico es el entregable que genera confianza más
rápido, porque el cliente descubre cosas de su propio warehouse que no sabía. Es más
persuasivo que cualquier propuesta.

## Qué se inventaria

### 1. Los activos que existen

```sql
-- Inventario completo de tablas con volumen, uso y antigüedad.
-- Se ejecuta por región; si el cliente tiene datasets en varias, se repite.
SELECT
  t.table_catalog AS project_id,
  t.table_schema AS dataset_id,
  t.table_name,
  t.table_type,
  s.row_count,
  ROUND(s.size_bytes / POW(1024, 3), 2) AS size_gb,
  TIMESTAMP_MILLIS(s.last_modified_time) AS last_modified_datetime,
  o.description AS table_description
FROM `region-us`.INFORMATION_SCHEMA.TABLES AS t
LEFT JOIN `region-us`.INFORMATION_SCHEMA.TABLE_STORAGE AS s
  USING (table_catalog, table_schema, table_name)
LEFT JOIN `region-us`.INFORMATION_SCHEMA.TABLE_OPTIONS AS o
  ON o.table_catalog = t.table_catalog
 AND o.table_schema = t.table_schema
 AND o.table_name = t.table_name
 AND o.option_name = 'description'
ORDER BY size_gb DESC
```

La columna que más dice es `last_modified_datetime`. En un warehouse sin gobierno, la
mitad de las tablas no se escriben hace más de un año. Eso no es un dato menor: son
tablas que alguien podría estar consultando creyendo que están vivas.

### 2. Las columnas y su clasificación

```sql
-- Export para el escaneo de Faro. El formato coincide con lo que espera
-- `faro scan --source information-schema`.
SELECT
  c.table_catalog,
  c.table_schema,
  c.table_name,
  c.column_name,
  c.data_type,
  c.is_nullable,
  f.description
FROM `region-us`.INFORMATION_SCHEMA.COLUMNS AS c
LEFT JOIN `region-us`.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS AS f
  ON  f.table_catalog = c.table_catalog
  AND f.table_schema  = c.table_schema
  AND f.table_name    = c.table_name
  AND f.field_path    = c.column_name
WHERE c.table_schema NOT LIKE '%_temp'
```

```bash
bq query --format=json --nouse_legacy_sql --max_rows=200000 < inventario.sql > columnas.json

faro scan \
  --input columnas.json \
  --source information-schema \
  --checks naming,privacy \
  --format md \
  --out diagnostico.md
```

### La alternativa preferible: leer la API directamente

Exportar `INFORMATION_SCHEMA` funciona, y tiene dos inconvenientes que aparecen justo en
la primera semana con un cliente nuevo: requiere permiso para **ejecutar consultas** —que
es exactamente el permiso que un cliente duda en dar a un proveedor externo— y factura
bytes procesados.

Leer los metadatos por la API no tiene ninguno de los dos:

```bash
faro scan --source bigquery-api \
  --project cliente-data-prod \
  --datasets analytics,reporting,sandbox \
  --checks naming,privacy \
  --format md \
  --out diagnostico.md
```

Basta `roles/bigquery.metadataViewer`. Costo cero. Y trae algo que
`INFORMATION_SCHEMA.COLUMNS` no expone: **las labels**, que son lo que permite inferir la
capa de un activo como una declaración del equipo dueño en lugar de una heurística sobre
el nombre del dataset.

Este camino está verificado de punta a punta en
[`tests/integration/test_bigquery_scan.py`](../tests/integration/test_bigquery_scan.py),
contra una API de BigQuery emulada.

Cualquiera de los dos produce el corazón del informe: cuántas columnas contienen datos
personales sin etiquetar, cuántas no tienen descripción, y qué proporción de nombres se
aparta de cualquier convención razonable.

Una nota sobre el escaneo inicial: conviene correrlo con `--fail-on none`. El objetivo
es medir, no bloquear, y un warehouse heredado va a producir miles de hallazgos. La
cifra agregada es el mensaje; la lista completa es un anexo.

### 3. Quién consume qué

Esta es la parte que casi nadie tiene y que más vale.

```sql
-- Grafo real de consumo, derivado del historial de jobs.
-- No depende de que nadie mantenga un mapa: es lo que efectivamente ocurrió.
SELECT
  ref.project_id,
  ref.dataset_id,
  ref.table_id,
  j.user_email AS consumer,
  COUNT(*) AS job_quantity,
  MAX(j.creation_time) AS last_seen_datetime,
  SUM(j.total_bytes_processed) / POW(1024, 4) AS total_tb_processed
FROM `region-us`.INFORMATION_SCHEMA.JOBS_BY_PROJECT AS j,
UNNEST(j.referenced_tables) AS ref
WHERE j.creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
  AND j.job_type = 'QUERY'
  AND j.state = 'DONE'
GROUP BY 1, 2, 3, 4
ORDER BY job_quantity DESC
```

Tres cosas salen siempre de esta consulta, y las tres son incómodas:

- **Tablas con cero consumo** que alguien mantiene con un job diario.
- **Consultas directas a producción** desde cuentas personales, saltándose el warehouse.
- **Dependencias no declaradas**: un área consumiendo una tabla de otra que no sabe que
  tiene consumidores, y que por lo tanto la cambia cuando quiere.

La tercera es la que justifica los contratos de datos mejor que cualquier argumento
teórico, porque el cliente puede ver el nombre concreto de la tabla y de la persona.

### 4. Los permisos efectivos

```bash
# Quién puede leer qué, a nivel de dataset.
for ds in $(bq ls --format=json | jq -r '.[].datasetReference.datasetId'); do
  echo "=== $ds"
  bq show --format=prettyjson "$ds" | jq -r '.access[] | "\(.role)\t\(.userByEmail // .groupByEmail // .specialGroup // .iamMember)"'
done
```

Lo que se busca: `specialGroup: projectReaders` sobre datasets con datos personales, o
cuentas individuales en lugar de grupos. Ambos son hallazgos, y el segundo es el que
hace imposible auditar los accesos después.

## El informe

Cuatro secciones, en este orden:

**1. Exposición de datos personales.** Va primero porque es lo que mueve a la
dirección. La forma efectiva es una tabla concreta:

| Dataset | Tablas con datos personales | Columnas sin etiquetar | Personas con acceso |
|---|---|---|---|
| `analytics` | 12 | 47 | 63 |
| `reporting` | 8 | 21 | 41 |
| `sandbox` | 19 | 88 | 112 |

`sandbox` siempre es el peor. Es donde se copian tablas "para una prueba" y se quedan.

**2. Estado del metadato.** Porcentaje de tablas y columnas con descripción,
puntaje de conformidad de nombres, y cuántos activos no tienen dueño identificable.

**3. Mapa de consumo.** Los diez activos más consultados, los consumidores externos por
activo, y la lista de tablas muertas.

**4. Recomendación.** Qué producto de datos conviene como piloto y por qué. El criterio
es doble: que tenga un consumidor real esperando algo, y que toque datos personales.
Así el mismo entregable demuestra valor y control.

## Cómo se decide si hay proyecto

Cuatro señales. Con dos basta; con ninguna, conviene decirlo:

- Más de 20 columnas con datos personales sin etiquetar en datasets de acceso amplio.
- Menos del 40% de las tablas con descripción.
- Dependencias cruzadas entre áreas sin contrato ni aviso.
- Un proyecto de IA o ML planificado para los próximos seis meses.

Si el diagnóstico sale limpio, decirlo tiene más valor que inventar un proyecto. La
recomendación honesta en ese caso es acotada: instalar el gate para que el warehouse no
se degrade, y volver a medir en seis meses.

## Errores típicos de esta fase

**Pedir accesos de administrador.** El inventario de activos y el escaneo por API se
hacen con `roles/bigquery.metadataViewer` y `roles/bigquery.resourceViewer`. Sólo el mapa
de consumo necesita ejecutar consultas, y se puede dejar para una segunda tanda una vez
que hay confianza. Ningún dato sale del proyecto del cliente, y decirlo explícitamente
destraba la conversación de seguridad.

**Escanear datos en vez de metadatos.** Es tentador para mejorar la clasificación, y
abre una discusión de confidencialidad que puede costar semanas. El metadato alcanza
para el diagnóstico.

**Presentar la lista completa de hallazgos.** Cuatro mil filas no producen decisión.
Producen parálisis, y a menudo la sensación de que el problema es demasiado grande.

---

Anterior: [Arquitectura de referencia](01-arquitectura-de-referencia.md) ·
Siguiente: [Fase 1 · Fundaciones](03-fase-1-fundaciones.md)
