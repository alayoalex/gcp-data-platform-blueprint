# ADR-0003 · Decidir con reglas deterministas; usar el LLM sólo para enriquecer

**Estado:** aceptada

## Contexto

La clasificación de datos personales y la evaluación de nombres son tareas donde un LLM
da buenos resultados. La tentación de construir el gate entero sobre un modelo es real,
y la plataforma de referencia que inspiró este blueprint lo hace así.

## Decisión

Un motor de reglas determinista decide qué bloquea. El LLM se invoca sólo para las
columnas que el catálogo no reconoció, y su salida es una sugerencia con nivel de
confianza, nunca un veredicto.

## Razones

**Determinismo.** Un gate que a veces deja pasar y a veces no el mismo nombre no es un
gate. Los equipos aprenden a reintentar el pipeline en lugar de corregir el problema, y
el control muere en un mes sin que nadie lo apague formalmente.

**Costo y latencia.** En un esquema maduro el catálogo resuelve más del 90% de las
columnas. Invocar al modelo para todas hace que el costo escale con el tamaño del
esquema en lugar de con lo desconocido.

**Auditabilidad.** Ante una pregunta de por qué una columna se clasificó así, la
respuesta es un patrón concreto en un archivo YAML versionado. "El modelo lo decidió" no
es una respuesta que sirva en una auditoría.

**Disponibilidad.** Si el LLM no responde —cuota, caída, timeout— el gate sigue
funcionando. Hay un test que lo verifica.

## Contrapartidas

- Las reglas no encuentran lo que no se llama como se espera. `campo_3` con RUTs adentro
  no lo detecta nadie sin mirar los datos.
- Mantener el catálogo es trabajo continuo, y es el trabajo que un LLM evitaría.

## Mitigación

El catálogo es el activo del servicio: mejora con cada implementación, y los patrones
que evitan falsos positivos son conocimiento ganado en campo. Para lo que no cubre,
[`llm.py`](../../governance/faro_governance/llm.py) existe y se activa con una línea.
