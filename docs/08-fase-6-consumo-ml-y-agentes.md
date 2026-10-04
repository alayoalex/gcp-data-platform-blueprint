# Fase 6 · Consumo: machine learning y agentes

**Duración típica:** 4 a 8 semanas
**Entregable:** features con corrección temporal, un modelo en producción y una capa
semántica que los agentes pueden consultar sin riesgo.

Es la fase que el cliente pide primero y que sólo se puede hacer al final. Vale la pena
decirlo en la primera reunión con el argumento concreto: **un agente sobre un warehouse
sin gobernar produce respuestas incorrectas con alta confianza**, y eso es peor que no
tener agente.

## Por qué el gobierno es prerrequisito y no un adorno

Un analista humano que encuentra una columna llamada `total` sabe que tiene que
preguntar qué incluye. Un agente no pregunta: elige la columna que mejor calza con la
pregunta y devuelve un número.

Eso convierte tres cosas que antes eran incomodidades en problemas de corrección:

| Sin gobierno | Qué hace el agente |
|---|---|
| Dos tablas con cifras de venta distintas | elige una, sin decir cuál ni por qué |
| Columna `total` sin descripción | asume qué significa |
| PII accesible en el warehouse | la incluye en una respuesta, en un chat, en un log |
| Métrica sin definición escrita | la inventa a partir del nombre |

Los tres primeros son consecuencias directas de lo que las fases anteriores resuelven.

## Features: el error que arruina modelos de churn

[`fea_customer_churn_daily`](../products/novamarket-commerce/definitions/feature/fea_customer_churn_daily.sqlx)
separa estructuralmente lo que se puede saber al momento del corte de lo que se observa
después:

```
features: ventana que TERMINA en snapshot_date
etiqueta:  ventana que EMPIEZA en snapshot_date
```

La tentación es calcular las features con una consulta sobre toda la tabla y la etiqueta
con otra. El resultado es un modelo con AUC 0.95 en validación y sin valor en
producción, porque alguna feature incorporó información posterior al corte.

Aquí la separación es verificable leyendo los `BETWEEN`, no una convención que hay que
recordar. Y un detalle que se pasa por alto: **el último corte útil es hace 90 días**.
Incluir cortes más recientes generaría filas con `is_churned_flag = false` que en
realidad significan "todavía no sabemos", y el modelo aprendería que los clientes
recientes nunca abandonan.

```sql
GENERATE_DATE_ARRAY(
  DATE '2021-01-01',
  DATE_SUB(CURRENT_DATE('America/Santiago'), INTERVAL 90 DAY),  -- el horizonte
  INTERVAL 7 DAY
)
```

### Qué aporta el contrato aquí

```yaml
ml:
  label_column: is_churned_flag
  label_horizon: P90D
  feature_windows: [P30D, P90D]
  point_in_time_correct: true
  excluded_from_features: [customer_key]
```

Sin esto, un modelo entrenado hoy no se puede auditar en seis meses. Con esto, cualquiera
puede verificar que el entrenamiento respetó lo declarado.

## El modelo

[`mdl_customer_churn`](../products/novamarket-commerce/definitions/feature/mdl_customer_churn.sqlx)
usa BigQuery ML. La decisión, argumentada en el propio archivo: el modelo se entrena
sobre una tabla que ya está en BigQuery, su salida se consume desde SQL, y el problema
cabe en un boosted tree. Sacar los datos a Vertex agregaría infraestructura, movimiento
de datos personales entre servicios y un paso de despliegue, a cambio de nada.

Vertex empieza a valer la pena cuando hace falta algo que BQML no da: arquitecturas
propias, entrenamiento distribuido, servicio en línea con baja latencia, o un registro de
modelos con gobierno propio. Empezar por BQML y migrar cuando haga falta es más barato
que al revés.

Un detalle que decide la calidad del modelo:

```sql
data_split_method = 'CUSTOM',
data_split_col = 'is_holdout_flag'
```

La partición aleatoria pondría cortes del mismo cliente en train y en test, y el modelo
memorizaría clientes en vez de aprender el patrón. El corte es temporal, que es como se
va a usar en producción.

```sql
-- Evaluación
SELECT * FROM ML.EVALUATE(MODEL `commerce_feature.mdl_customer_churn`);

-- Qué pesa en la predicción. Vale la pena mirarlo antes de creerle al modelo:
-- si `tenure_day_quantity` domina, probablemente esté aprendiendo la antigüedad
-- de la base y no el comportamiento.
SELECT * FROM ML.GLOBAL_EXPLAIN(MODEL `commerce_feature.mdl_customer_churn`);

-- Inferencia
SELECT customer_key, predicted_is_churned_flag_probs[OFFSET(0)].prob AS churn_probability
FROM ML.PREDICT(
  MODEL `commerce_feature.mdl_customer_churn`,
  (SELECT * FROM `commerce_feature.fea_customer_churn_daily`
   WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM `commerce_feature.fea_customer_churn_daily`))
)
```

## La capa semántica para agentes

[`sem_commerce_overview`](../products/novamarket-commerce/definitions/gold/sem_commerce_overview.sqlx)
es una vista ancha y explícita. No agrega información; **quita ambigüedad**, que para un
agente es lo mismo que agregar precisión.

Tres reglas al construirla:

1. **Una sola definición por métrica.** Si `net_sales_amount` aparece ahí, no debe haber
   otra columna en la superficie expuesta que también se llame "venta".
2. **Nada que identifique personas.** Los agentes son el consumidor menos predecible de
   la plataforma; lo que ven puede terminar en una respuesta, en un chat o en un log.
3. **Descripciones escritas para ser leídas por un modelo**: explícitas sobre unidad,
   grano y exclusiones.

La descripción de la columna dice literalmente *"Es LA cifra de venta: no existe otra
definición válida"*. Es una instrucción, y está ahí a propósito.

## Cómo se conecta el agente

```python
from anthropic import Anthropic

client = Anthropic()

CONSULTAR_VENTAS = {
    "name": "consultar_ventas",
    "description": (
        "Consulta la capa semantica de comercio de NovaMarket. Devuelve ventas netas, "
        "unidades, clientes y conversion por dia, departamento y producto. "
        "La venta neta excluye ordenes canceladas. Las fechas son dia calendario en "
        "America/Santiago. No contiene datos de clientes identificables."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "fecha_desde": {"type": "string", "format": "date"},
            "fecha_hasta": {"type": "string", "format": "date"},
            "department_name": {"type": "string"},
            "metrica": {
                "type": "string",
                "enum": ["net_sales_amount", "item_quantity", "customer_quantity",
                         "order_quantity", "conversion_percentage"],
            },
        },
        "required": ["fecha_desde", "fecha_hasta", "metrica"],
    },
}

respuesta = client.messages.create(
    model="claude-sonnet-5",
    max_tokens=2048,
    tools=[CONSULTAR_VENTAS],
    messages=[{"role": "user",
               "content": "¿Cómo viene la venta de lácteos este mes contra el anterior?"}],
)
```

La herramienta expone **parámetros, no SQL libre**. La diferencia es de seguridad y de
corrección a la vez:

- El agente no puede consultar nada fuera de la vista semántica.
- El enum de `metrica` hace imposible que invente una columna.
- El service account del agente tiene lectura sólo sobre `sem_commerce_overview`, y
  ninguna sobre la taxonomía `person`. Aunque alguien exponga SQL libre por error, los
  policy tags hacen que la consulta falle en lugar de devolver datos personales.

Ese último punto es la razón por la que el control por columna de la fase 1 importa
aquí: es la red que sostiene cuando la capa de aplicación se equivoca.

## Costos con agentes

Un agente sobre BigQuery puede generar consultas caras sin intención. Tres controles que
conviene poner antes de abrir el acceso, no después de la primera factura:

```sql
-- 1. La vista semántica sobre tablas particionadas, y siempre filtrando por fecha.
-- 2. Cuota diaria en el proyecto del agente:
ALTER PROJECT `novamarket-data-prod-c3d4`
SET OPTIONS (default_query_job_timeout_ms = 60000);
-- 3. Reserva de slots separada, para que un agente en bucle no compita con los
--    pipelines de producción.
```

Y una tabla materializada por encima de la vista si el volumen lo justifica: es más
barato pagar una materialización diaria que mil consultas ad hoc sobre las tablas base.

## Errores típicos

**Dar acceso SQL libre al agente.** Funciona en la demo y produce respuestas
inventadas en producción, porque el agente encuentra tablas de trabajo, tablas muertas
y versiones antiguas del mismo dato.

**Exponer silver.** Los seudónimos son datos personales bajo la Ley 21.719 y el grano es
por persona. La superficie para agentes es gold agregado.

**Entrenar sobre gold en vez de sobre features.** Gold está agregado para consumo
analítico, no para entrenamiento. La corrección temporal se pierde en el camino.

**Presentar el agente antes de tener gobierno.** Impresiona en la demo y destruye la
confianza la primera vez que da un número equivocado con seguridad. Recuperarla cuesta
mucho más que el mes que habría tomado hacerlo en orden.

---

Anterior: [Fase 5 · Productos y contratos](07-fase-5-productos-y-contratos.md) ·
Siguiente: [Operación](09-operacion.md)
