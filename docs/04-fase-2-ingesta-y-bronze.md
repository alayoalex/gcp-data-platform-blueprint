# Fase 2 · Ingesta y capa bronze

**Duración típica:** 1 a 2 semanas por fuente
**Entregable:** los datos del origen aterrizando en BigQuery, fielmente y con auditoría.
**Código:** [`infra/terraform/datastream.tf`](../infra/terraform/datastream.tf) ·
[`ingestion/`](../ingestion/) ·
[`products/novamarket-commerce/definitions/bronze/`](../products/novamarket-commerce/definitions/bronze/)

## La regla de bronze

**Bronze no transforma.** No renombra columnas, no castea tipos, no filtra filas, no
corrige nada.

Cuesta aceptarlo porque muchos problemas se arreglarían con una línea aquí. La razón
para no hacerlo aparece seis meses después, cuando se descubre que una regla de negocio
estaba mal desde el principio y hay que reconstruir silver desde el origen. Si bronze
"arregló" algo en el camino, esa reconstrucción ya no es posible: el dato original se
perdió y la única fuente es el sistema transaccional, que hace rato sobrescribió su
historia.

Bronze sí agrega tres columnas, porque son metadato de la ingesta y no alteran el dato:

| Columna | Para qué |
|---|---|
| `ingestion_datetime` | Cuándo llegó. Es la clave de partición y el criterio del incremental. |
| `source_system_name` | De qué sistema vino. Deja de ser obvio con la segunda fuente. |
| `is_deleted_flag` | Si la fila fue borrada en el origen. El CDC lo informa; perderlo significa que el warehouse conserva filas que ya no existen. |

## Dos rutas de ingesta

### CDC gestionado, para bases transaccionales

[Datastream](../infra/terraform/datastream.tf) replica PostgreSQL a BigQuery de forma
continua.

La pregunta de si construir esto a mano tiene una respuesta corta: la alternativa
honesta a un CDC gestionado no es "un script simple", es reimplementar el manejo de
esquemas cambiantes, el backfill inicial, el reintento tras una caída de red y el orden
de los eventos. Ese trabajo tiene sentido cuando hay una razón concreta —un motor que
Datastream no soporta, una restricción de red insalvable— y no antes.

Lo que sí hay que decidir, y no es automático:

**Qué tablas se replican.** `include_objects` es una lista explícita, nunca un comodín.
Replicar la base entera trae las tablas de sesiones, de colas y de auditoría del
framework, que no aportan nada analítico y sí arrastran datos personales que después
alguien tiene que clasificar, gobernar y eventualmente borrar.

**Qué columnas no se replican.**

```hcl
exclude_objects {
  postgresql_schemas {
    schema = "public"
    postgresql_tables {
      table = "users"
      postgresql_columns { column = "hashed_password" }
    }
  }
}
```

`hashed_password` se excluye en el origen, no se filtra después. Un dato que nunca sale
de la base transaccional no puede filtrarse desde el warehouse, y eso es cualitativamente
distinto de un dato que llega y se descarta en la siguiente capa.

**Cuánta latencia.** `data_freshness = "900s"` significa que BigQuery aplica los cambios
cada quince minutos. El costo baja bastante al agrupar, y ningún consumidor de esta
plataforma necesita menos. Si mañana alguien lo necesita, que lo pida con un caso de uso
y un presupuesto: bajar a 60s multiplica el costo de aplicación por un factor que
sorprende.

### Carga de archivos, para todo lo demás

La mitad del trabajo real son fuentes sin CDC: el catálogo que un proveedor deja en un
bucket cada noche, el export de la plataforma de marketing, el CSV mensual.
[`ingestion/functions/pg_export_to_bronze/`](../ingestion/functions/pg_export_to_bronze/main.py)
cubre ese caso con tres reglas que separan una ingesta operable de una que produce
incidentes silenciosos:

**El esquema se declara, no se infiere.** La autodetección de BigQuery funciona hasta
el día en que un archivo llega con una columna vacía y el tipo cambia de `INT64` a
`STRING`. Lo que sigue es una tabla que ya no se puede consultar y un lunes perdido.
Cada feed tiene su contrato en
[`ingestion/schemas/`](../ingestion/schemas/supplier_catalog.json).

**Los archivos malos se apartan, no se descartan.** Un archivo rechazado va a un
prefijo de cuarentena con el motivo en un `.reason.txt` al lado. Descartarlo deja un
hueco en los datos que nadie nota hasta el cierre de mes.

**La ingesta es idempotente.** El destino de la carga es la partición del día, no la
tabla completa:

```python
table_id = f"{PROJECT_ID}.{BRONZE_DATASET}.{config.target_table}${partition_date}"
```

Reprocesar un archivo reemplaza sólo su día. Es la diferencia entre "corregimos el
martes" y "perdimos el trimestre". Los reintentos de Cloud Run no son hipotéticos:
ocurren.

## La fábrica de tablas bronze

Todas las tablas bronze hacen exactamente lo mismo. Escribir eso treinta veces a mano
garantiza que treinta tablas terminen ligeramente distintas entre sí, y que la número
diecisiete tenga un `QUALIFY` mal escrito que nadie note.

Por eso [`includes/bronze.js`](../products/novamarket-commerce/includes/bronze.js)
declara el patrón una vez y cada tabla queda en una llamada:

```javascript
cdcLanding({
  name: "raw_shop_order",
  sourceTable: "orders",
  primaryKey: "order_id",
  description: "Copia fiel de las ordenes de compra registradas por la tienda.",
  tags: ["hourly"],
  columns: { order_id: "Identificador de la orden...", /* ... */ },
});
```

Las descripciones sí se escriben una por una. No hay forma de generarlas: son
conocimiento del negocio, y el gate las exige precisamente porque son la parte que no
se puede automatizar.

### Deduplicación

El stream trae todas las versiones de cada fila. Bronze conserva sólo la última
conocida:

```sql
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY src.order_id
  ORDER BY src.datastream_metadata.source_timestamp DESC
) = 1
```

Reconstruir el historial completo —saber que la orden estuvo `pending` antes de estar
`completed`— es trabajo de una tabla SCD tipo 2 en silver, no de la capa de aterrizaje.
Mezclar ambas cosas produce una tabla bronze que ya no es una copia del origen y que
tampoco es un historial correcto.

## El nombre de las tablas bronze

Convención: `raw_<sistema_origen>_<entidad>`, en singular.

```
raw_shop_user        raw_shop_order        raw_shop_order_item
raw_shop_product     raw_shop_event        raw_supplier_catalog
```

`shop` es el sistema, no el negocio. Cuando llegue la segunda fuente —el ERP, la
plataforma de marketing— `raw_erp_customer` y `raw_shop_user` van a convivir
representando cosas parecidas pero no iguales, y el prefijo es lo que evita la
discusión de cuál es "la tabla de clientes".

El gate aplica esta convención con severidad `WARN` en bronze, no `BLOCK`. La razón
está en [`naming.yaml`](../governance/faro_governance/rules/naming.yaml): los nombres de
las columnas en bronze vienen impuestos por el origen, y exigir ahí lo mismo que en gold
obligaría a renombrar, que es exactamente lo que bronze no debe hacer.

## Verificación local con un emulador

La ingesta es la capa donde más barato sale equivocarse y más caro sale descubrirlo en
producción, así que conviene poder probarla sin nube.

[`docker-compose.yml`](../docker-compose.yml) levanta [Floci](https://floci.io), un
emulador local de GCP:

```bash
make emulator           # levanta el emulador en el puerto 4588
make test-integration   # corre los tests de ingesta contra él
make emulator-stop
```

Qué cubre y qué no, porque conviene saberlo antes de invertir tiempo en intentarlo:

| Servicio | Estado en el emulador |
|---|---|
| Cloud Storage | emulación completa (REST JSON y XML) |
| BigQuery | sólo metadatos, inserts en streaming y un subconjunto de SQL |
| Data Catalog / policy tags | no soportado |
| Dataform | no soportado |
| Datastream | no soportado |

Dicho de otro modo: **el emulador sirve para la capa de ingesta, no para validar el
modelo de datos ni el de privacidad.** Las transformaciones de silver y gold necesitan
un motor GoogleSQL real, y los policy tags necesitan Data Catalog; ambas cosas se
verifican contra un proyecto GCP de desarrollo.

Lo que sí se verifica, y no es poco, es todo el comportamiento sobre Storage —que es
donde están los errores que un test unitario no ve:

- El archivo rechazado se **copia** a cuarentena, no se pierde.
- El motivo queda escrito en un `.reason.txt` al lado.
- El original se elimina del prefijo de entrada.
- **El evento de escritura que genera la propia cuarentena no dispara otra ingesta.**
  Esta es la que importa: si la ruta de cuarentena volviera a calzar con el patrón de
  entrada, la función se llamaría a sí misma indefinidamente, y eso se descubre en la
  factura.

Los tests que no necesitan el emulador —el enrutamiento, que es la lógica que más se
equivoca— corren siempre, en
[`ingestion/tests/test_routing.py`](../ingestion/tests/test_routing.py). Los que sí lo
necesitan viven en [`tests/integration/`](../tests/integration/) y se **saltan** cuando
el emulador no está arriba, en lugar de fallar: un test de integración
que pone rojo el pipeline de quien no levantó la dependencia enseña al equipo a ignorar
el rojo.

## Verificación contra GCP

```sql
-- 1. ¿Llegaron datos y están frescos?
SELECT
  'raw_shop_order' AS table_name,
  COUNT(*) AS row_quantity,
  MAX(ingestion_datetime) AS last_ingestion_datetime,
  TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(ingestion_datetime), MINUTE) AS lag_minutes
FROM `novamarket-data-dev-a1b2.commerce_bronze.raw_shop_order`;

-- 2. ¿La deduplicación funcionó? Debe devolver cero filas.
SELECT order_id, COUNT(*) AS version_quantity
FROM `novamarket-data-dev-a1b2.commerce_bronze.raw_shop_order`
GROUP BY order_id
HAVING COUNT(*) > 1;

-- 3. ¿Los conteos cuadran con el origen?
--    Se ejecuta contra PostgreSQL y se compara a mano la primera vez.
--    SELECT COUNT(*) FROM orders WHERE created_at < '<corte>';
```

La tercera es tediosa y es la única que detecta un backfill incompleto. Vale la pena
hacerla una vez por fuente, con un corte fijo, y dejar el número anotado.

## Errores típicos

**Replicar la base completa.** Trae las tablas del framework, las de sesiones y las de
auditoría. Cada una es trabajo de clasificación que nadie pidió.

**Usar `WRITE_TRUNCATE` sobre la tabla completa en la carga de archivos.** Reprocesar
un archivo de marzo borra abril. La partición como destino lo evita.

**Poner el `is_deleted_flag` en el filtro de bronze.** Filtrar los borrados
aquí significa que el warehouse no puede distinguir "nunca existió" de "se borró", y
esa distinción importa para auditoría. El filtro va en silver.

**Dar a Datastream un usuario con más permisos de los necesarios.** El usuario de
replicación necesita `REPLICATION` y `SELECT` sobre las tablas replicadas. Nada más.

---

Anterior: [Fase 1 · Fundaciones](03-fase-1-fundaciones.md) ·
Siguiente: [Fase 3 · Silver y gold](05-fase-3-silver-y-gold.md)
