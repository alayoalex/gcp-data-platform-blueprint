# Fase 3 · Silver y gold

**Duración típica:** 2 a 4 semanas por producto de datos
**Entregable:** el modelo conformado y los productos de datos que el negocio consume.
**Código:** [`definitions/silver/`](../products/novamarket-commerce/definitions/silver/) ·
[`definitions/gold/`](../products/novamarket-commerce/definitions/gold/)

## Silver: donde ocurre el gobierno

Silver hace cuatro cosas, y la cuarta es la que define la plataforma.

### 1. Corregir los nombres

El origen tiene los nombres que tiene. Silver es el único punto donde se traducen a la
convención, y a partir de ahí nadie vuelve a ver los originales.

| Origen | Silver | Por qué |
|---|---|---|
| `order_dow` | `order_day_of_week` | `dow` no significa nada fuera del sistema que lo inventó |
| `order_hour` | `order_hour_of_day` | ¿hora del día, o duración en horas? |
| `days_since_prior` | `days_since_prior_order_quantity` | ¿anterior qué? |
| `total_amount` | `order_total_amount` | el sufijo `_amount` declara que es dinero |
| `reordered` | `is_reordered_flag` | los booleanos se anuncian |

Esta traducción es exactamente lo que el gate verifica. Ver
[la convención completa](ref-naming.md).

### 2. Normalizar tipos y zonas horarias

```sql
${helpers.businessDate("ord.created_at")} AS order_date
```

Parece trivial y es la fuente más común de discrepancias entre un reporte y el sistema
transaccional: una venta de las 21:30 en Santiago cae al día siguiente si se agrupa en
UTC. La zona horaria del negocio se declara una vez en
[`workflow_settings.yaml`](../products/novamarket-commerce/workflow_settings.yaml) y se
aplica siempre a través de
[`helpers.businessDate`](../products/novamarket-commerce/includes/helpers.js).

La alternativa —que cada modelador escriba su `DATE(..., 'America/Santiago')`— funciona
hasta que uno lo olvida, y entonces hay dos definiciones de "día" conviviendo en el
mismo warehouse sin que nadie lo note hasta el cierre de mes.

### 3. Desnormalizar lo que todos necesitan

[`mst_product`](../products/novamarket-commerce/definitions/silver/mst_product.sqlx)
resuelve la jerarquía de catálogo —producto, pasillo, departamento— para que cada
consumidor no repita el mismo join de tres tablas. Es el tipo de decisión que parece
contraria a la normalización y que en un warehouse columnar no cuesta casi nada: el
almacenamiento es barato, y el join repetido cuesta en cada consulta y en cada
oportunidad de escribirlo mal.

### 4. Seudonimizar

Esta es la razón por la que silver existe como capa separada.

```sql
${helpers.pseudonymize("usr.id")} AS customer_key
```

[`mst_customer`](../products/novamarket-commerce/definitions/silver/mst_customer.sqlx)
toma tres decisiones, en orden de importancia:

**`user_id` no cruza a silver.** Cruza `customer_key`, su hash con sal. Un identificador
interno parece inocuo, pero es la llave que reconecta cualquier tabla de esta capa con
la identidad real; convertirlo en seudónimo rompe esa cadena para todo lo que esté
aguas abajo, de una sola vez.

**El correo se reduce a su dominio.** Casi siempre que alguien pide "el email" en un
modelo analítico, la pregunta real es de segmentación —corporativo contra personal— y
el dominio la responde. Vale la pena preguntar antes de copiar la columna entera.

**El teléfono se convierte en un booleano.** `has_phone_flag`. Nadie llama por teléfono
desde un dashboard.

### La bóveda de identidad

Quien necesite el dato identificable de verdad —soporte, cumplimiento, una solicitud de
titular— lo busca en
[`mst_customer_identity`](../products/novamarket-commerce/definitions/silver/mst_customer_identity.sqlx).
Es la única tabla con correo, nombre, teléfono y dirección, con policy tags por columna
y su propia lista de acceso.

El punto de concentrar en vez de eliminar: el dato personal no desaparece de la
plataforma —desaparecerlo sería imposible y tampoco deseable, porque hay obligaciones
legales que exigen poder responder qué se tiene de una persona— pero se concentra en un
activo con una lista de acceso, en vez de diluirse en cincuenta tablas donde nadie sabe
quién lo puede leer.

El gate reconoce este patrón: una tabla con la label `data_vault: "true"` queda exenta
de la exigencia de seudonimización, **siempre que toda columna clasificada lleve su
policy tag**. La exención se paga con etiquetado; una bóveda sin etiquetar es
simplemente una tabla con datos personales. Ver
[`privacy.py`](../governance/faro_governance/privacy.py).

### Sobre la sal

```javascript
const pseudonymSaltRef = environment === "prod"
  ? "`" + projectId + "`.commerce_udf.pseudonym_salt()"
  : "'dev-salt-no-secreta'";
```

La sal tiene que ser dos cosas a la vez, y por eso vive en Secret Manager y no en el
código:

- **Estable**: si cambia, todos los seudónimos cambian y se pierde la trazabilidad
  histórica del cliente. Rotarla es posible, y es un evento caro que obliga a
  reprocesar.
- **Secreta**: sin ella, un atacante con la tabla de hashes reconstruye los
  identificadores por fuerza bruta. El espacio de enteros de una tabla de usuarios es
  pequeño; el de los correos, también.

Un hash sin sal no es una seudonimización. Es una ofuscación, y la diferencia importa
legalmente: el dato seudonimizado sigue siendo dato personal bajo la Ley 21.719, pero
el tratamiento razonable de la clave es lo que distingue una medida de seguridad de un
gesto.

## Gold: productos de datos

Gold no es "silver agregado". Es el conjunto de tablas que alguien de fuera del equipo
de datos consume, y esa diferencia impone tres obligaciones que silver no tiene:
contrato, dueño identificable y SLO.

### Las decisiones de negocio se escriben tres veces

```sql
WHERE NOT ord.is_cancelled_flag
```

Excluir las órdenes canceladas parece un detalle técnico y es una definición de
negocio: determina si "venta" significa "vendido" o "facturado". En
[`dmt_sales_daily`](../products/novamarket-commerce/definitions/gold/dmt_sales_daily.sqlx)
está escrita en tres lugares:

1. En el `WHERE` de la consulta.
2. En la descripción del activo, que va al catálogo de BigQuery.
3. En el [contrato](../products/novamarket-commerce/contracts/dmt_sales_daily.yaml).

No es redundancia ociosa. Quien lea el dashboard no va a leer el SQL, y el agente de IA
que consulte la tabla va a leer la descripción y no el `WHERE`.

### Un error caro y silencioso

En [`dmt_customer_rfm_monthly`](../products/novamarket-commerce/definitions/gold/dmt_customer_rfm_monthly.sqlx):

```sql
NTILE(5) OVER (PARTITION BY metric_month_date ORDER BY recency_day_quantity DESC)
```

Los quintiles se calculan **dentro de cada mes**, no sobre toda la historia. Un cliente
no debería cambiar de segmento porque la base de clientes creció: el segmento mide su
posición relativa entre sus contemporáneos.

Calcularlo global es el error silencioso más común en un RFM, y sólo se nota cuando
alguien pregunta por qué todos los clientes antiguos son "champion". Para entonces
llevan meses recibiendo campañas equivocadas.

Este es el tipo de error que ningún gate detecta. El gate verifica que la tabla se llame
bien, esté documentada y tenga contrato; que la métrica esté bien definida sigue siendo
trabajo humano, y conviene ser explícito al respecto cuando se presenta la plataforma.

### Nombres en gold

`<dmt|rpt>_<producto>_<alcance>[_<grano>]`

```
dmt_sales_daily              dmt_funnel_daily       dmt_customer_rfm_monthly
```

`dmt` por *data mart*. El grano en el nombre —`daily`, `monthly`— evita la pregunta más
frecuente sobre cualquier tabla agregada, y evita también que aparezcan dos tablas que
se llaman igual y no lo son.

## Aserciones

Dataform las ejecuta después de cada materialización. Hay dos clases, y la segunda es
la que menos se usa y más vale.

**Estructurales**, declaradas en el config:

```javascript
assertions: {
  uniqueKey: ["sale_date", "product_key"],
  nonNull: ["sale_date", "product_key", "net_sales_amount"]
}
```

**De negocio y de gobierno**, escritas como consultas que deben devolver cero filas:

- [`assert_gold_freshness`](../products/novamarket-commerce/definitions/assertions/assert_gold_freshness.sqlx):
  falla si un producto quedó más atrasado que la frescura prometida en su contrato. Es
  lo que convierte el contrato en algo exigible y no en un documento.
- [`assert_no_pii_outside_vault`](../products/novamarket-commerce/definitions/assertions/assert_no_pii_outside_vault.sqlx):
  busca columnas con nombre de dato personal fuera de la bóveda.

La segunda parece redundante con el gate de CI, y no lo es. El gate revisa lo que pasa
por el repositorio; esta aserción revisa lo que hay en el warehouse. Una tabla creada a
mano desde la consola para "una prueba rápida", un export de un proveedor o un job de
otro equipo con permisos sobre el dataset nunca pasaron por un merge request. Los dos
controles miran el mismo riesgo desde lados distintos del despliegue, y el segundo es el
que detecta lo que nunca fue revisado.

## Errores típicos

**Saltarse silver.** "Es una capa más que mantener." Es la capa donde ocurre la
seudonimización; sin ella, cada tabla gold decide por su cuenta qué hacer con los datos
personales, y basta una omisión para perder la propiedad.

**Que gold consuma bronze directamente.** Ocurre por apuro y elimina la frontera de
privacidad en ese camino. Es lo que la aserción de PII detecta.

**Agregar de más en gold.** Una tabla gold demasiado agregada obliga a que cada pregunta
nueva vuelva a silver. El grano correcto es el más fino que el consumidor pueda usar,
no el más resumido que quepa.

**Poner reglas de negocio en la capa de BI.** Ahí no se versionan, no se testean y no se
pueden reusar. Si dos dashboards calculan la venta neta por su cuenta, en algún momento
van a diferir.

---

Anterior: [Fase 2 · Ingesta y bronze](04-fase-2-ingesta-y-bronze.md) ·
Siguiente: [Fase 4 · Gobierno como código](06-fase-4-gobierno-como-codigo.md)
