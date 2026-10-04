# ADR-0002 · Usar Dataform en vez de dbt

**Estado:** aceptada

## Contexto

Ambas herramientas resuelven lo mismo: SQL versionado, referencias entre modelos, tests
y documentación. La elección es de contexto, no de mérito técnico.

## Decisión

Dataform, con la salvedad explícita de que el motor de gobierno no depende de esa
elección.

## Razones

- **Servicio gestionado de GCP.** No hay que operar un runner ni pagar suscripción. Para
  un cliente sin equipo de plataforma, esa diferencia es decisiva.
- **Policy tags nativos.** `bigqueryPolicyTags` en la definición de columna. En dbt hay
  que resolverlo con macros o post-hooks, y esa indirección es exactamente donde se
  pierden los tags al promover entre entornos.
- **`dataform compile --json` produce el grafo completo** con descripciones y tags, sin
  ejecutar nada contra BigQuery. Es precisamente la entrada que el gate necesita.
- **JavaScript en los includes.** La fábrica de tablas bronze de este blueprint es una
  función. En dbt sería una macro Jinja, que es menos legible para el mismo resultado.

## Contrapartidas

- El ecosistema de dbt es más grande y sus paquetes de tests son mejores.
- Encontrar gente que conozca dbt es más fácil.
- Dataform ata la plataforma a GCP. Es una atadura que ya existe por BigQuery.

## Mitigación

El motor de gobierno opera sobre `DataAsset`, no sobre Dataform. Leer un manifest de dbt
requiere un adaptador de unas treinta líneas en
[`parser.py`](../../governance/faro_governance/parser.py). Si un cliente ya usa dbt, no
hay que migrarlo.
