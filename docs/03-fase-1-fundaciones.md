# Fase 1 · Fundaciones

**Duración típica:** 1 a 2 semanas
**Entregable:** proyectos, datasets, taxonomías de policy tags e IAM, todo en Terraform.
**Código:** [`infra/terraform/`](../infra/terraform/)

## Lo que se decide aquí y es caro cambiar después

Tres decisiones de esta fase se vuelven costosas de revertir. Conviene tomarlas
despacio, porque todo lo demás del proyecto se puede rehacer en una tarde.

### 1. La separación de proyectos

```
novamarket-data-dev-a1b2      warehouse de desarrollo
novamarket-data-prod-c3d4     warehouse de producción
novamarket-governance-a1b2    taxonomías de policy tags (uno solo, transversal)
```

Dev y prod separados es estándar. Lo que no es obvio es **el tercero**.

Las taxonomías viven en su propio proyecto porque son transversales a todos los
productos y a todos los entornos. Si vivieran dentro del proyecto de datos, cada
entorno tendría su propio conjunto de tags con ids distintos, y una tabla promovida de
dev a prod perdería su etiquetado en el camino —silenciosamente, porque el id apuntaría
a un tag inexistente y BigQuery simplemente no aplicaría el control.

Es un error que se descubre tarde y mal: en una auditoría, o cuando alguien nota que en
producción la columna que debía estar protegida se lee sin restricción.

### 2. La granularidad de los datasets

Por capa, no por tema. Ya argumentado en
[la arquitectura](01-arquitectura-de-referencia.md#los-datasets); el resumen es que
"los analistas leen gold" cabe en una frase y se audita de un vistazo.

Cambiarlo después implica mover tablas entre datasets, lo que significa recrearlas y
actualizar cada referencia, cada dashboard y cada permiso.

### 3. Los grupos de IAM

**Nunca cuentas individuales en un binding.** Siempre grupos de Google.

El argumento no es la elegancia: es que cuando una persona se va, el proceso de baja
la quita del grupo y el acceso desaparece en todas partes. Con bindings individuales,
el acceso queda hasta que alguien recuerde revisar dataset por dataset. En un warehouse
de treinta datasets, nadie recuerda.

```hcl
owner_group           = "datos-comercio@novamarket.cl"   # dueño del producto
analyst_group         = "analistas@novamarket.cl"        # consumidores de gold
privacy_officer_group = "privacidad@novamarket.cl"       # únicos que leen PII
```

`privacy_officer_group` es el que hay que mantener pequeño. Es el único con
`categoryFineGrainedReader` sobre la taxonomía `person`, y por lo tanto el único que
puede leer las columnas etiquetadas. El policy tag hace que el control sea exigible; el
tamaño del grupo es el control.

## Cómo funcionan los policy tags

Vale detenerse porque el mecanismo no es evidente y es el corazón del modelo.

Una columna etiquetada con un policy tag deja de ser legible para quien no tenga
`roles/datacatalog.categoryFineGrainedReader` sobre ese tag. Y la parte importante:
**la consulta falla, no devuelve nulos.**

```sql
-- Ejecutado por alguien sin acceso a la taxonomía `person`:
SELECT * FROM commerce_silver.mst_customer_identity;
-- Access Denied: User does not have permission to access policy tag
-- "person.contact" on column customer_email.

-- La misma persona, pidiendo sólo lo que puede ver:
SELECT customer_key, account_created_date FROM commerce_silver.mst_customer_identity;
-- Funciona.
```

Que falle en vez de degradar es lo que hace útil el mecanismo: obliga a que quien
escribe SQL sea explícito sobre si necesita el dato personal, en lugar de arrastrarlo
sin pensar con un `SELECT *`. El control deja de depender de la disciplina de quien
consulta.

### Ser dueño de la tabla no implica poder leerla

En [`taxonomies.tf`](../infra/terraform/taxonomies.tf), el único principal con
`categoryFineGrainedReader` sobre `person` es `privacy_officer_group`. Ni el grupo
dueño del producto ni el service account que ejecuta las transformaciones lo tienen.

Esto sorprende la primera vez, y es intencional. El service account **escribe** esas
columnas dentro de una consulta de Dataform, pero una persona del equipo dueño no
puede **leerlas** desde la consola. Separar la operación del acceso al contenido es
precisamente lo que permite decir que el dato está protegido: si el dueño pudiera
leerlo, el control sería declarativo y no real.

## Cerrar el circuito entre Terraform y Dataform

Este es el detalle de implementación que más problemas causa y menos se documenta.

`includes/policy_tags.js` necesita los ids reales de los policy tags, que Terraform
genera. Escribirlos a mano funciona hasta que alguien recrea una taxonomía: todos los
ids cambian, y a partir de ahí cada despliegue de silver falla con un error que no
menciona policy tags por ninguna parte.

La solución es que el pipeline de infraestructura escriba el archivo:

```bash
# Al final del apply de infraestructura:
terraform output -json policy_tag_ids \
  > ../../products/novamarket-commerce/includes/policy_tags.generated.json
```

```javascript
// includes/policy_tags.js lee el archivo generado en vez de literales.
const generated = require("includes/policy_tags.generated.json");
```

El output ya está declarado en [`outputs.tf`](../infra/terraform/outputs.tf). El
archivo `policy_tags.js` de este blueprint usa ids de ejemplo para que el repositorio
sea legible sin haber ejecutado Terraform; el comentario en el archivo lo advierte.

## Orden de ejecución

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # completar con los valores reales

terraform init
terraform plan -out=plan.tfplan
terraform apply plan.tfplan
```

El orden importa: las taxonomías antes que los datasets, y los datasets antes que
Dataform. Terraform lo resuelve solo a partir de las dependencias, pero conviene
saberlo para leer el plan.

## Verificación

No basta con que `apply` termine sin error. Cuatro comprobaciones:

```bash
# 1. Los datasets existen y tienen las labels de gobierno.
bq show --format=prettyjson novamarket-data-dev-a1b2:commerce_gold | jq '.labels'
# Debe mostrar layer, product, environment, managed_by.

# 2. Las taxonomías existen en el proyecto de gobierno.
gcloud data-catalog taxonomies list \
  --location=us-central1 --project=novamarket-governance-a1b2

# 3. El SA del producto puede escribir en sus datasets.
bq show --format=prettyjson novamarket-data-dev-a1b2:commerce_silver | jq '.access'

# 4. El control de columna funciona de verdad.
#    Esta es la única que prueba algo: las tres anteriores verifican que los recursos
#    existen, no que protejan. Ejecutar como alguien fuera de privacy_officer_group.
bq query --nouse_legacy_sql \
  'SELECT customer_email FROM `novamarket-data-dev-a1b2.commerce_silver.mst_customer_identity` LIMIT 1'
# Debe fallar con Access Denied.
```

La cuarta se hace después de la fase 3, cuando la tabla exista. Vale la pena agendarla:
es la única que demuestra que el modelo de privacidad funciona, y es exactamente la
prueba que el cliente va a querer ver.

## Errores típicos

**Crear las taxonomías a mano desde la consola.** Se hace en cinco minutos y cuesta
meses: los ids quedan fuera de Terraform, nadie sabe quién los creó, y el día que hay
que replicar el entorno no hay de dónde partir.

**Dar `roles/bigquery.admin` al service account del producto.** Necesita `dataEditor`
sobre sus datasets y `jobUser` en el proyecto. Nada más. Un SA con admin puede borrar
datasets de otros productos, y los service accounts no tienen quien los supervise.

**Olvidar `deletion_protection` en producción.** Está en
[`variables.tf`](../infra/terraform/variables.tf) con default `true` justamente por
eso. En dev se desactiva para poder iterar.

**Una sola taxonomía con todos los tags.** Es tentador porque simplifica el Terraform,
y rompe la granularidad del control: `categoryFineGrainedReader` se otorga sobre la
taxonomía completa, así que quien pueda leer costos comerciales podría leer también
datos de salud. Las taxonomías se separan por quién debe poder leerlas, no por
categoría semántica.

---

Anterior: [Fase 0 · Descubrimiento](02-fase-0-descubrimiento.md) ·
Siguiente: [Fase 2 · Ingesta y bronze](04-fase-2-ingesta-y-bronze.md)
