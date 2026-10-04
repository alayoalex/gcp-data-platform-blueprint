# ADR-0001 · Aplicar el gobierno en el merge request, no en revisiones posteriores

**Estado:** aceptada

## Contexto

El gobierno de datos se implementa habitualmente como supervisión: un comité define
estándares, se compra un catálogo, se nombran responsables, se revisa periódicamente.
Esa estructura puede estar bien hecha y no cambiar nada, porque el momento en que se
decide si una tabla se llama bien es cuando alguien la escribe, y en ese momento no hay
comité ni catálogo mirando.

## Decisión

Las reglas de gobierno se escriben en archivos que un programa lee, y se verifican
automáticamente en cada merge request. Un hallazgo bloqueante impide el merge.

## Consecuencias

**A favor**

- El costo de cumplir baja hasta casi desaparecer: corregir un nombre mientras se
  escribe la consulta toma segundos.
- El estándar deja de ser negociable caso a caso. La conversación pasa de "¿te parece
  bien este nombre?" a "¿está bien la regla?", que es discutible en frío y una sola vez.
- El gobierno se vuelve demostrable: es consecuencia mecánica de un control que corre en
  cada despliegue y deja registro.

**En contra**

- Sólo cubre lo que pasa por el repositorio. Una tabla creada a mano desde la consola no
  se evalúa. Se mitiga con aserciones en tiempo de ejecución, que son el control
  complementario.
- Requiere que el equipo trabaje con merge requests. En una organización donde se
  escriben consultas directamente en la consola de BigQuery, hay que cambiar eso primero,
  y ese cambio es más difícil que instalar el gate.
- Un gate mal calibrado genera rechazo y termina desactivado. La progresión de adopción
  descrita en la fase 4 no es opcional.
