# Blueprint de plataforma de datos gobernada en GCP

Cómo se construye, desde cero y por fases, una plataforma de datos en Google Cloud
donde el gobierno no es un proyecto aparte sino una propiedad del pipeline de
despliegue.

El caso es **NovaMarket**, un e-commerce ficticio. Su base transaccional es la del
proyecto [`01-ecommerce-analytics-platform`](../01-ecommerce-analytics-platform):
cuentas de usuario con correo, teléfono y dirección; órdenes; catálogo de productos;
clickstream. Es deliberadamente ordinario. Cualquier empresa con una aplicación y una
base de datos tiene esta forma, y por eso sirve de molde.

## El problema que resuelve

Una empresa mediana llega con datos y sin gobierno. Los síntomas siempre son los
mismos, y ninguno se presenta como un problema de gobierno:

- Dos áreas reportan cifras de venta distintas y nadie puede decir cuál está bien.
- Nadie sabe en cuántas tablas está el correo de los clientes.
- Un cambio en una tabla rompe un dashboard y se descubre por el reclamo de alguien.
- El equipo quiere entrenar un modelo o conectar un agente de IA, y el primer mes se
  va en entender qué significa cada columna.

La respuesta habitual —comprar un catálogo de datos, escribir un estándar de
nomenclatura, nombrar data stewards— falla por la misma razón: **depende de que la
gente recuerde cumplirla**. Este blueprint toma el camino contrario. Las reglas se
escriben una vez, en archivos que el pipeline lee, y se verifican en cada pull
request. Un nombre mal puesto no llega a producción porque el merge no ocurre.

## Cómo está organizado

| Carpeta | Qué contiene |
|---|---|
| [`docs/`](docs/) | El proceso de construcción por fases. Es lo que hay que leer primero. |
| [`governance/`](governance/) | **Faro**: el motor de políticas. Python, sin dependencias de GCP. |
| [`products/novamarket-commerce/`](products/novamarket-commerce/) | El producto de datos en Dataform: bronze → silver → gold → feature, con contratos. |
| [`infra/terraform/`](infra/terraform/) | Datasets, taxonomías de policy tags, IAM, Datastream. |
| [`ingestion/`](ingestion/) | La ruta de ingesta para fuentes sin CDC. |
| [`examples/`](examples/) | Fixtures de compilación para demostrar el gate sin GCP. |
| [`.gitlab-ci.yml`](.gitlab-ci.yml) | El pipeline: tests, compilación, gate y despliegue. |
| [`docker-compose.yml`](docker-compose.yml) | Emulador local de GCP para los tests de ingesta. |

## Empezar

```bash
make setup     # crea el venv e instala Faro
make test      # 123 tests, sin nube ni red
make demo      # el gate sobre el producto tal como está: aprueba
make demo-bad  # el gate sobre un MR con violaciones: bloquea
```

Y opcionalmente, contra un emulador local de GCP:

```bash
make emulator           # levanta Floci en el puerto 4588
make test-integration   # ingesta sobre GCS + escaneo sobre la API de BigQuery
make emulator-stop
```

Lo que el emulador cubre y lo que no está en
[`tests/integration/README.md`](tests/integration/README.md). El resumen: sirve para la
capa de ingesta y para el escaneo de descubrimiento, no para validar las
transformaciones ni los policy tags.

`make demo-bad` es la demostración que conviene mostrar primero. Simula un pull
request realista —un analista con prisa crea una tabla `CustomerContactList` en gold
con el correo y el teléfono en claro, y de paso elimina una columna que finanzas
consume— y produce esto:

```
[BLOCK] CustomerContactList  (naming.table)
         Criterios no satisfechos: snake_case, layer_structure, documented.
         -> dmt_customer_contact_list
[BLOCK] CustomerContactList  (contract.missing)
         El activo esta en la capa GOLD y no tiene contrato de datos.
[BLOCK] CustomerContactList.email  (privacy.treatment)
         'email' es CONFIDENTIAL y la politica no permite dato identificable en la capa GOLD.
         -> Seudonimizar en silver y exponer solo la clave seudonimizada aguas abajo.
[BLOCK] dmt_sales_daily.net_sales_amount  (contract.breaking_change)
         El contrato promete la columna 'net_sales_amount' y el activo no la expone.
         -> Restaurar, o publicar version mayor y notificar a equipo-finanzas@novamarket.cl...
```

Ninguna de esas cuatro cosas la habría detectado una revisión de código humana con
fiabilidad. Las cuatro se detectan en dos segundos, antes del merge, con la corrección
concreta al lado.

## El recorrido

Las fases están pensadas para leerse en orden. Cada una produce algo verificable y
ninguna depende de que la siguiente exista.

| | Documento | Qué se construye |
|---|---|---|
| | [Visión y modelo de servicio](docs/00-vision-y-modelo-de-servicio.md) | Por qué gobierno-como-código, y cómo se vende |
| | [Arquitectura de referencia](docs/01-arquitectura-de-referencia.md) | El diagrama completo y las decisiones de fondo |
| 0 | [Descubrimiento](docs/02-fase-0-descubrimiento.md) | Inventario del warehouse actual y diagnóstico |
| 1 | [Fundaciones](docs/03-fase-1-fundaciones.md) | Proyectos, datasets, taxonomías, IAM |
| 2 | [Ingesta y bronze](docs/04-fase-2-ingesta-y-bronze.md) | CDC, carga de archivos, capa de aterrizaje |
| 3 | [Silver y gold](docs/05-fase-3-silver-y-gold.md) | Modelo conformado y productos de datos |
| 4 | [Gobierno como código](docs/06-fase-4-gobierno-como-codigo.md) | El motor de políticas y el gate de CI |
| 5 | [Productos y contratos](docs/07-fase-5-productos-y-contratos.md) | Contratos, catálogo, ciclo de vida |
| 6 | [Consumo: ML y agentes](docs/08-fase-6-consumo-ml-y-agentes.md) | Features, BigQuery ML, capa semántica para agentes |
| | [Operación](docs/09-operacion.md) | SLOs, costos, linaje, incidentes |

Referencias transversales:
[convención de nombres](docs/ref-naming.md) ·
[modelo de clasificación de datos](docs/ref-clasificacion.md) ·
[decisiones de arquitectura](docs/adr/)

## Qué es real y qué no

Conviene ser explícito, porque un blueprint que exagera lo que tiene tiene menos valor
que uno honesto sobre sus límites:

- **Corre y está probado, sin red**: el motor de políticas (`governance/`) y el
  enrutamiento de la ingesta. 123 tests en menos de un segundo. Las demos del gate
  funcionan tal cual, hoy, sin GCP.
- **Verificado contra una API de GCP emulada** (12 tests, `make emulator`): el
  comportamiento de la ingesta sobre Cloud Storage —cuarentena, motivo, borrado del
  original, no reentrada— y el **escaneo de descubrimiento de la fase 0** contra la API
  de BigQuery, incluyendo lectura de tipos reales, labels y generación del informe.
- **Está completo pero no se ha desplegado**: el Terraform, el producto Dataform y la
  carga a bronze. Es código real y coherente entre sí, escrito contra las APIs actuales,
  pero no se aplicó contra un proyecto GCP en el desarrollo de este blueprint. No hay
  Terraform ni el CLI de Dataform instalados en este entorno, así que
  `terraform validate` y `dataform compile` no se ejecutaron, y el emulador no cubre
  Dataform, Data Catalog, Datastream ni los load jobs de BigQuery. Espera ajustes al
  desplegarlo por primera vez.
- **Es ilustrativo**: los ids de policy tags en `includes/policy_tags.js`, los ids de
  proyecto y los nombres de grupos. Se reemplazan con los outputs reales de Terraform.
- **Es ficticio**: NovaMarket, sus equipos y sus direcciones de correo.

## Origen

La estructura general —evaluación automatizada de activos de datos en el merge request,
con agentes de clasificación, memoria de evaluaciones y gates por capa— está inspirada
en la plataforma de gobierno de datos de una aerolínea grande. Aquí está generalizada,
simplificada y reconstruida desde cero para una empresa sin ese presupuesto: sin
GenAI gateway propio, sin servicio de memoria compartida, sin catálogo empresarial.
Lo que queda es el patrón, que es la parte transferible.

Las diferencias deliberadas respecto de esa referencia están documentadas en
[`docs/adr/`](docs/adr/).
