# Referencia · Convención de nombres

La política completa vive en
[`governance/faro_governance/rules/naming.yaml`](../governance/faro_governance/rules/naming.yaml).
Ese archivo **es** la convención; este documento la explica. Si ambos difieren, manda el
YAML, porque es el que se ejecuta.

## Datasets

```
<dominio>_<capa>
```

```
commerce_bronze   commerce_silver   commerce_gold   commerce_feature   commerce_work
```

## Tablas, por capa

| Capa | Patrón | Ejemplos |
|---|---|---|
| Bronze | `raw_<sistema>_<entidad>` | `raw_shop_order`, `raw_supplier_catalog` |
| Silver | `<mst\|trn\|evt>_<entidad>[_<calificador>]` | `mst_customer`, `trn_order_item`, `evt_session_event` |
| Gold | `<dmt\|rpt\|sem>_<producto>_<alcance>[_<grano>]` | `dmt_sales_daily`, `sem_commerce_overview` |
| Feature | `fea_<entidad>_<propósito>_<grano>` | `fea_customer_churn_daily` |

Los prefijos de silver distinguen la naturaleza del activo, no su contenido:

- `mst_` — maestro: una fila por entidad, estado actual (`mst_customer`, `mst_product`).
- `trn_` — transaccional: una fila por hecho ocurrido (`trn_order`, `trn_order_item`).
- `evt_` — evento: una fila por suceso con marca de tiempo (`evt_session_event`).

Los de gold describen el propósito, no el tipo físico:

- `dmt_` — data mart: tabla agregada para consumo analítico.
- `rpt_` — reporte: activo construido para una salida concreta.
- `sem_` — capa semántica: la superficie que consumen los agentes de IA.

Deliberadamente no hay un prefijo `vw_`. Al consumidor no le importa si detrás hay una
vista o una tabla materializada —y esa decisión puede cambiar por rendimiento sin que
nada más cambie. Lo que sí le importa es para qué sirve el activo.

**Singular siempre.** `mst_customer`, no `mst_customers`. La tabla contiene clientes,
pero cada fila es un cliente, y el nombre describe la fila. Es una convención arbitraria
como cualquier otra; lo que importa es que sea una sola.

## Columnas

### Sufijos por categoría semántica

| Categoría | Sufijos aceptados | Se infiere de | Ejemplo |
|---|---|---|---|
| Identificador | `_id`, `_key` | nombre contiene id/key/uuid | `customer_key` |
| Fecha | `_date` | tipo `DATE` | `sale_date` |
| Marca de tiempo | `_datetime`, `_timestamp`, `_at` | tipo `TIMESTAMP`/`DATETIME` | `ingestion_datetime` |
| Booleano | `_flag`, o prefijo `is_`/`has_` | tipo `BOOL` | `is_cancelled_flag` |
| Monto | `_amount`, `_price`, `_cost` | nombre sugiere dinero | `net_sales_amount` |
| Porcentaje | `_percentage`, `_rate`, `_ratio` | nombre sugiere proporción | `conversion_percentage` |
| Cantidad | `_quantity`, `_count`, `_number` | nombre sugiere conteo | `item_quantity` |
| Nombre | `_name` | nombre/label/title | `product_name` |
| Descripción | `_description`, `_text`, `_comment` | descripción/nota | `product_description` |
| Código | `_code`, `_status`, `_type` | código/estado/tipo | `order_status_code` |

Cuando el tipo no es concluyente —`STRING`, `INT64` y `NUMERIC` cubren casi todo— y el
nombre no da pistas, la categoría queda **libre** y no se exige sufijo.

Eso es deliberado. La tentación es asumir que todo `INT64` es una cantidad, y eso
marcaría `order_hour` o `day_of_week` como infracciones, llenando el gate de ruido. Un
gate ruidoso se desactiva; uno prudente se respeta.

### Ventanas temporales

`net_amount_90d` es correcto: la categoría es `amount` y `_90d` califica la ventana de
cálculo. El sufijo de ventana se ignora al verificar la categoría.

Patrón aceptado: `_<n><d|w|m|y>` o `_<n>days|weeks|months|years`.

### Columnas de sistema

Exentas de la regla de categoría, porque su nombre es parte del contrato de la
plataforma:

```
ingestion_datetime   source_system_name   record_hash_code   is_deleted_flag
valid_from_datetime  valid_to_datetime    is_current_flag    ingestion_date
```

## Abreviaturas

**Prohibidas** (extracto — la lista completa está en el YAML):

| | | | |
|---|---|---|---|
| `qty` → quantity | `amt` → amount | `num` → number | `cnt` → count |
| `desc` → description | `dt` → date | `ts` → timestamp | `dow` → day_of_week |
| `addr` → address | `cust` → customer | `prod` → product | `txn` → transaction |
| `pct` → percentage | `avg` → average | `tot` → total | `val` → value |

**Autorizadas** (siglas de uso establecido):

```
id  url  uri  ip  sku  utm  iso  api  gmv  aov  ltv  rfm  csv  json  utc  at
cl  us  br  pe  ar  clp  usd  brl
```

Esta lista es la que más se personaliza por cliente: lo que en una empresa es jerga
interna aceptada, en otra es ruido. Es también el primer archivo que hay que editar al
implementar en un cliente nuevo — sin él, el gate marca `sku` y `gmv` como infracciones
y pierde credibilidad en la primera corrida.

## Puntuación

Pesos por tipo de activo (suman 1.0):

| Criterio | Tabla | Columna | Dataset |
|---|---|---|---|
| `snake_case` | 0.22 | 0.22 | 0.30 |
| `full_words` | 0.22 | 0.22 | 0.28 |
| `layer_structure` | 0.20 | — | 0.24 |
| `data_category` | — | 0.22 | — |
| `no_redundancy` | 0.14 | 0.12 | — |
| `readable` | 0.10 | 0.10 | 0.08 |
| `documented` | 0.12 | 0.12 | 0.10 |

Umbrales: tabla y columna 0.80, dataset 0.85.

Severidad por capa: bronze `WARN`, el resto `BLOCK`.

## Consultar la convención desde la terminal

```bash
faro suggest order_dow --type INT64      # → order_day_of_week
faro suggest total --type NUMERIC        # → total_amount
faro suggest sales_daily --layer GOLD    # → dmt_sales_daily
faro suggest net_amt_90d --type NUMERIC  # → net_amount_90d
```

Es la forma más rápida de resolver una duda mientras se escribe el modelo, y evita la
discusión de nombres en la revisión del merge request.

---

Volver al [índice](../README.md)
