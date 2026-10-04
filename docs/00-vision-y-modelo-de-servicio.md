# Visión y modelo de servicio

## La tesis

El gobierno de datos fracasa cuando se implementa como una capa de supervisión sobre
el trabajo, y funciona cuando se implementa como una propiedad del trabajo.

La versión de supervisión es la conocida: un comité define estándares, se compra un
catálogo, se nombran responsables por dominio, se hacen revisiones trimestrales. Todo
eso puede estar bien hecho y aun así no cambiar nada, porque el punto donde se decide
si una tabla se llama bien es el momento en que alguien la escribe, y en ese momento no
hay comité, ni catálogo, ni responsable mirando. Hay una persona con una tarea y una
fecha.

La versión que funciona interviene exactamente ahí. Las mismas reglas —nomenclatura,
clasificación de datos personales, documentación obligatoria, contratos— se escriben
en archivos que un programa puede leer, y se verifican automáticamente en cada pull
request. El resultado no es que la gente cumpla más; es que incumplir deja de ser
posible sin que alguien lo note.

Tres consecuencias que no son obvias hasta que se ven funcionando:

**El costo de cumplir baja hasta desaparecer.** Corregir un nombre mientras se escribe
la consulta toma segundos. Corregirlo seis meses después implica encontrar a todos los
consumidores, coordinar una ventana, migrar y deprecar. Es el mismo trabajo hecho en
dos momentos con dos órdenes de magnitud de diferencia en costo.

**El estándar deja de ser negociable en cada caso.** Cuando la regla la aplica un
programa, la conversación deja de ser "¿te parece que este nombre está bien?" y pasa a
ser "¿está bien la regla?". La segunda pregunta es mucho mejor: es discutible en frío,
una vez, y su resultado es un cambio en un archivo YAML.

**El gobierno se vuelve demostrable.** "Ninguna tabla de la capa gold contiene datos
personales identificables" deja de ser una afirmación de confianza y pasa a ser una
consecuencia mecánica de un control que corre en cada despliegue y deja registro.

## Por qué ahora

Durante veinte años el argumento para gobernar datos fue el cumplimiento normativo y
la confianza en los reportes. Eran buenos argumentos, y perdieron sistemáticamente
contra la urgencia de entregar.

Los modelos de lenguaje cambiaron la ecuación, y no por la razón que se suele dar. No
es que la IA "requiera datos limpios" —eso ya se decía del machine learning clásico.
Es algo más concreto: **un agente de IA consume el esquema como si fuera documentación,
y actúa sobre lo que entiende.** Un analista humano que encuentra una columna llamada
`total` sabe que tiene que preguntar qué incluye. Un agente no pregunta: elige, y
devuelve un número con la misma seguridad con que devolvería el correcto.

Eso convierte la calidad del metadato en un problema de corrección, no de comodidad.
Una tabla mal nombrada y sin descripción ya no es deuda técnica; es una fuente de
respuestas equivocadas dichas con confianza, a escala y sin trazabilidad.

Y en Chile, la Ley 21.719 —vigente desde diciembre de 2026— agrega la otra mitad del
argumento: sanciones reales por tratar datos personales sin base de licitud, y la
obligación de responder solicitudes de titulares en plazos acotados. Responder "qué
datos tienen de mí" es trivial si hay una bóveda de identidad y un catálogo; es un
proyecto de tres semanas si el correo del cliente está esparcido en cuarenta tablas.

## El modelo de servicio

Un cliente típico —entre 50 y 500 empleados, con datos y sin equipo de gobierno— se
atiende en cuatro tramos. Cada uno entrega algo que funciona por sí solo, porque el
que no se puede cortar sin perder lo hecho es el que no se vende.

### Tramo 1 · Diagnóstico (2 a 3 semanas)

Inventario del warehouse actual, clasificación automática de datos personales, mapa de
consumidores reales y una evaluación de la nomenclatura existente. Se ejecuta con el
mismo motor del blueprint en modo escaneo: `faro scan` sobre un export de
`INFORMATION_SCHEMA`.

Entregable: un informe con cifras, no con opiniones. "De 412 tablas, 38 contienen
datos personales sin etiquetar; 7 de ellas están en datasets que 60 personas pueden
leer; el 71% de las columnas no tiene descripción."

Es el tramo que más rápido genera confianza, porque el cliente descubre cosas de su
propio warehouse que no sabía. También es el que define si hay proyecto: si el
diagnóstico sale limpio, conviene decirlo.

### Tramo 2 · Fundaciones (4 a 6 semanas)

Proyectos y datasets por capa, taxonomías de policy tags, IAM por grupo, ingesta de
una fuente, y el gate de CI operativo sobre un producto de datos piloto.

Entregable: un producto de datos completo, en producción, bajo contrato, más el
pipeline que impide que el siguiente se construya mal.

La elección del piloto importa más que su tamaño. Sirve uno que tenga un consumidor
real esperándolo y que toque datos personales: así el cliente ve el valor y el control
en el mismo entregable.

### Tramo 3 · Escalamiento (2 a 4 meses)

Migración de los productos de datos existentes al modelo, uno por uno, y traspaso de
la operación al equipo interno.

Este tramo es principalmente formación disfrazada de migración. El objetivo explícito
es que al terminar el equipo del cliente construya productos nuevos sin ayuda. Un
servicio de gobierno que deja al cliente dependiente fracasó, aunque facture.

### Tramo 4 · Habilitación de IA (4 a 8 semanas)

Capa semántica sobre los productos gold, tablas de features con corrección temporal,
y el conector para que los agentes consulten sólo lo que tienen permitido.

Es el tramo que el cliente pide primero y que sólo se puede hacer al final. Vale la
pena decirlo en la primera reunión, con el argumento concreto: un agente sobre un
warehouse sin gobernar produce respuestas incorrectas con alta confianza, y eso es
peor que no tener agente.

## Qué hace defendible esta oferta

No es la tecnología: BigQuery, Dataform y Terraform los usa cualquiera. Son tres cosas
que se acumulan con cada cliente:

1. **El catálogo de clasificación.** `privacy.yaml` mejora con cada implementación.
   Los patrones que evitan falsos positivos son conocimiento ganado en campo, no algo
   que se deduzca en una pizarra.
2. **Las convenciones calibradas.** La lista de abreviaturas prohibidas y de siglas
   aceptadas es distinta en retail que en salud o en banca. Tener versiones por
   industria es un activo.
3. **El gate como producto.** El motor es código propio, portable, que corre igual en
   GitLab CI, GitHub Actions o Cloud Build. No hay licencia que renovar ni proveedor
   del que depender, y eso se puede decir en una propuesta.

## Lo que no hay que prometer

- **Que el gate reemplaza el criterio.** Detecta lo mecánico, que es la mayoría del
  volumen y nada de la dificultad. Que una métrica esté bien definida sigue siendo
  trabajo humano.
- **Que la clasificación automática es completa.** Encuentra lo que se llama como se
  espera. Una columna `campo_3` con RUTs adentro no la encuentra nadie sin mirar los
  datos.
- **Que esto ordena la organización.** Ordena el warehouse. Si dos áreas no se ponen
  de acuerdo en qué es una venta, el gate va a dejar pasar dos definiciones bien
  nombradas y bien documentadas de cosas distintas.

---

Siguiente: [Arquitectura de referencia](01-arquitectura-de-referencia.md)
