# ADR-0005 · No construir un servicio de memoria de evaluaciones

**Estado:** aceptada

## Contexto

La plataforma de referencia que inspiró este blueprint incluye un microservicio de
memoria compartida: cachea las evaluaciones de cada activo por id, evita reevaluar lo no
cambiado, y permite corrección humana de los resultados de los agentes.

Es una buena pieza de ingeniería y resuelve un problema real a su escala: miles de
activos, evaluaciones que cuestan dinero porque son llamadas a un LLM, y equipos
distribuidos que necesitan consistencia entre entornos.

## Decisión

No construirlo. El gate reevalúa todo en cada corrida.

## Razones

**El costo que la caché evita aquí es cero.** La evaluación es un motor de reglas local:
la suite completa corre en menos de un segundo, y un esquema de 400 tablas se evalúa en menos de un
segundo. No hay nada que cachear.

**Agrega un servicio que hay que operar.** Una base de datos, un despliegue, un esquema
que migrar, y una fuente nueva de fallos en el camino crítico del CI. Para un cliente sin
equipo de plataforma, cada servicio adicional es una carga permanente.

**La corrección humana tiene un lugar mejor.** En la plataforma de referencia, un humano
corrige el resultado del agente y esa corrección se guarda en la memoria. Aquí la
corrección va al archivo de política —una exclusión nueva en `privacy.yaml`, una sigla
en `allowed_acronyms`— donde queda versionada, revisada en un merge request y aplicada a
todos los casos futuros, no sólo a esa columna.

## Cuándo reconsiderar

Si se activa el clasificador LLM sobre un warehouse grande, la caché vuelve a tener
sentido: ahí sí hay un costo por evaluación. La forma más barata de agregarla en ese
escenario no es un microservicio sino una tabla de BigQuery con
`(asset_id, schema_hash, classification)`, consultada antes de invocar al modelo.

Construir el microservicio sería la tercera opción, y sólo si hay varios equipos
consumiendo las evaluaciones desde sistemas distintos.
