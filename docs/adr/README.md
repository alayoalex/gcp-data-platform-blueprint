# Decisiones de arquitectura

Registro de las decisiones de fondo, con su contexto y sus contrapartidas. Están aquí y
no dentro de los documentos de fase porque sobreviven a los cambios de implementación:
el código puede reescribirse entero sin que la decisión cambie.

| | Decisión |
|---|---|
| [0001](0001-gobierno-en-el-merge-request.md) | Aplicar el gobierno en el merge request, no en revisiones posteriores |
| [0002](0002-dataform-sobre-dbt.md) | Usar Dataform en vez de dbt |
| [0003](0003-reglas-antes-que-llm.md) | Decidir con reglas deterministas; usar el LLM sólo para enriquecer |
| [0004](0004-boveda-de-identidad.md) | Concentrar el dato personal en una bóveda en vez de eliminarlo |
| [0005](0005-sin-servicio-de-memoria-compartida.md) | No construir un servicio de memoria de evaluaciones |

Las decisiones 0003 y 0005 son diferencias deliberadas respecto de la plataforma de
referencia que inspiró este blueprint. Ambas se explican por la misma razón: un cliente
sin equipo de plataforma paga cada servicio adicional todos los días, y esas dos piezas
resuelven problemas que a esta escala no existen.
