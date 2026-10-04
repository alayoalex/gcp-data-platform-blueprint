# Fase 4 · Gobierno como código

**Duración típica:** 1 a 2 semanas
**Entregable:** el gate corriendo en cada merge request, con políticas calibradas.
**Código:** [`governance/`](../governance/) · [`.gitlab-ci.yml`](../.gitlab-ci.yml) ·
[`scripts/gitlab_mr_note.py`](../scripts/gitlab_mr_note.py)

Esta es la fase que distingue esta plataforma de un warehouse bien hecho.

## La arquitectura del motor

```
Compilación de Dataform (JSON)
        │
        ▼
  parser.py ─────────────►  DataAsset  (modelo agnóstico de la herramienta)
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
   naming.py               privacy.py             contracts.py
   reglas + pesos       catálogo + LLM          esquema vs. promesa
        │                       │                       │
        └───────────────────────┼───────────────────────┘
                                ▼
                          Assessment[]
                                │
                          report.py  ──┬►  Markdown para la nota del MR
                                │      └►  Code Quality para el widget de GitLab
                                ▼
                          cli.py ──►  código de salida 0 / 1
```

Dos propiedades del diseño que importan más de lo que parece:

**El motor no conoce BigQuery ni Dataform.** Conoce `DataAsset`. Los adaptadores de
[`parser.py`](../governance/faro_governance/parser.py) traducen. Eso permite leer un
manifest de dbt, un export de `INFORMATION_SCHEMA` o cualquier otra cosa con un
adaptador de treinta líneas, sin tocar las políticas.

**Las políticas viven en YAML, no en Python.** [`naming.yaml`](../governance/faro_governance/rules/naming.yaml)
y [`privacy.yaml`](../governance/faro_governance/rules/privacy.yaml) **son** la política;
el código sólo la interpreta. Adaptar la plataforma a otra empresa es editar esos dos
archivos.

Esa separación es también lo que hace vendible el servicio: el catálogo de patrones de
privacidad y la lista de abreviaturas calibradas por industria son el activo que se
acumula con cada implementación.

## Cómo se puntúa un nombre

Cada criterio da entre 0 y 1, y se pondera:

```yaml
weights:
  column:
    snake_case: 0.22
    full_words: 0.22
    data_category: 0.22
    no_redundancy: 0.12
    readable: 0.10
    documented: 0.12
```

Puntaje continuo y no una lista de aprobado/reprobado, por dos razones:

- **Un nombre puede estar mal de muchas maneras a la vez**, y el puntaje lo refleja
  proporcionalmente. `total_amt` falla en `full_words` y en `data_category`, y su
  puntaje lo muestra.
- **El umbral es una perilla.** Empezar en 0.6 para un warehouse heredado y subirlo a
  0.8 en tres meses es una estrategia de adopción; con reglas binarias sólo se puede
  activar o desactivar.

### Las reglas, en concreto

| Criterio | Qué verifica | Ejemplo que falla |
|---|---|---|
| `snake_case` | minúsculas y guion bajo | `CustomerContactList` |
| `full_words` | palabras completas o siglas autorizadas | `total_amt`, `usr_id`, `order_dow` |
| `data_category` | sufijo acorde al tipo y la semántica | `sale_day` en un `DATE` |
| `layer_structure` | prefijo acorde a la capa | `sales_daily` en gold (falta `dmt_`) |
| `no_redundancy` | sin tokens repetidos ni vacíos | `order_order_id`, `product_data` |
| `readable` | menos de 50 caracteres, hasta 6 tokens | nombres de tres líneas |
| `documented` | descripción presente y con contenido | descripción vacía o que repite el nombre |

### La severidad depende de la capa

```yaml
severity_by_layer:
  BRONZE: WARN
  SILVER: BLOCK
  GOLD: BLOCK
  FEATURE: BLOCK
```

Un nombre malo en bronze es el nombre que puso el sistema de origen y corregirlo
implicaría transformar, que es justamente lo que bronze no debe hacer. Un nombre malo
en gold llega al consumidor final y a los agentes de IA.

## Cómo se clasifica una columna

[`privacy.yaml`](../governance/faro_governance/rules/privacy.yaml) mapea patrones de
nombre a categoría, sensibilidad y policy tag. **El orden importa**: gana la primera
regla que calza, así que las más específicas van arriba.

Ese detalle no es teórico. Durante la construcción de este blueprint, un test detectó
que `ip_address` se clasificaba como ubicación física porque el patrón
`(^|_)address($|_)` de la categoría `location` estaba antes que `ip_address` en
`online_identifier`. La corrección fue reordenar el catálogo.

### Evitar falsos positivos es tan importante como detectar

```yaml
exclusions:
  - '_domain_name$'   # `email_domain_name` ES la mitigación, no el riesgo
  - '_flag$'          # `has_email_flag` es un booleano de preferencia
  - '_count$'         # un contador de correos no es un correo
  - '_hash_code$'
```

Un gate que marca `email_count` como dato de contacto pierde la confianza del equipo en
una semana, y un gate en el que nadie confía se desactiva. Las exclusiones no son
concesiones: son lo que mantiene el control vivo.

### El contexto ajusta la confianza

Una columna `full_name` en `customer_silver.mst_customer` es casi con certeza el nombre
de una persona. La misma columna en `catalog_silver.mst_product` es el nombre de un
producto. `PrivacyClassifier._confidence` sube o baja la confianza según el dataset y la
tabla, y por debajo de 0.70 el hallazgo se marca para revisión humana en vez de
decidirse solo.

Los identificadores fuertes son la excepción: un RUT es un RUT en cualquier tabla.

### La matriz de tratamiento

Qué se exige según sensibilidad y capa:

| | BRONZE | SILVER | GOLD | FEATURE |
|---|---|---|---|---|
| **RESTRICTED** | permitido | seudonimizar | prohibido | prohibido |
| **CONFIDENTIAL** | permitido | enmascarar | prohibido | seudonimizar |
| **INTERNAL** | permitido | permitido | permitido | permitido |

Bronze permite todo porque es la copia fiel y su control es de acceso, no de forma. Las
credenciales son la excepción absoluta: están prohibidas en las cuatro capas, porque no
tienen ningún uso analítico.

## Dónde entra el LLM

Sólo para las columnas que el catálogo no reconoció, y **no decide si algo bloquea**.

[`llm.py`](../governance/faro_governance/llm.py) implementa el clasificador sobre Vertex
AI con tres restricciones deliberadas:

1. **`temperature=0.0`**: la clasificación no debe variar entre corridas.
2. **Sólo metadatos**: nombre, tipo y descripción. Nunca filas de ejemplo. Mandar datos
   reales a un modelo mejora la precisión y es exactamente lo que no se puede hacer
   cuando el objetivo del ejercicio es proteger esos datos.
3. **Falla en blando**: si el LLM no responde, el gate sigue funcionando con las reglas.
   Hay un test que lo verifica.

## El gate en GitLab CI

El [pipeline](../.gitlab-ci.yml) tiene cuatro etapas: `test`, `compile`, `gate` y
`deploy`.

### Compilar y evaluar son dos jobs

Porque necesitan runtimes distintos —Node para el CLI de Dataform, Python para Faro— y
separarlos evita mantener una imagen con ambos. El artefacto que los une es un solo JSON:

```yaml
compile:
  image: node:20-slim
  script:
    - dataform compile --json > "$CI_PROJECT_DIR/compilation.json"
  artifacts:
    paths: [compilation.json]

gate:
  image: python:3.12-slim
  needs: [compile]
```

`dataform compile` no consulta BigQuery: resuelve refs, includes y variables de entorno
de forma local. No necesita credenciales de GCP, así que ese job puede correr en un
runner sin acceso a la nube.

**Se evalúa la compilación, no los archivos `.sqlx`.** La compilación produce el grafo
completo tal como BigQuery lo va a recibir. Evaluar los archivos fuente sería evaluar
una aproximación.

### Dos salidas, porque GitLab tiene dos superficies

```yaml
- faro check ... --format codequality --path-prefix "$DATAFORM_DIR"
                 --out gl-code-quality-report.json --fail-on none
- faro check ... --format md --out gate-report.md --fail-on none
- python scripts/gitlab_mr_note.py gate-report.md
- faro check ... --format text --fail-on block      # el veredicto, al final
```

**El widget del merge request** se alimenta del reporte
[Code Quality](https://docs.gitlab.com/ee/ci/testing/code_quality.html), que GitLab
entiende de forma nativa:

```yaml
artifacts:
  when: always
  reports:
    codequality: gl-code-quality-report.json
```

Los hallazgos aparecen anclados a archivo y línea, y —esto es lo valioso— **GitLab
distingue solo cuáles son nuevos respecto de la rama base**. El equipo ve lo que
introdujo este MR, no la deuda histórica completa.

Para que funcione hay dos detalles que deciden si la integración sirve o no:

- **El `fingerprint` tiene que ser estable.** Es lo que GitLab usa para saber qué
  hallazgo ya existía. Se calcula sobre la identidad del hallazgo —activo, objetivo,
  control— y no sobre su mensaje, que puede reformularse sin que el problema cambie. Si
  variara entre corridas, cada push mostraría todo como nuevo y el widget dejaría de
  significar algo.
- **Las rutas tienen que ser relativas a la raíz del repositorio.** La compilación de
  Dataform las entrega relativas al proyecto Dataform, y `--path-prefix` las completa.
  Los hallazgos de contrato ya vienen relativas a la raíz, así que el motor no antepone
  el prefijo dos veces. Hay tests para ambos casos.

**La nota del merge request** da el resumen que el widget no da: el veredicto en una
línea. [`scripts/gitlab_mr_note.py`](../scripts/gitlab_mr_note.py) la publica y la
actualiza en cada push, identificándola por un marcador HTML invisible. La alternativa
—una nota nueva por corrida— convierte un MR con cinco iteraciones en un hilo de cinco
reportes contradictorios que nadie lee.

Dos advertencias prácticas sobre ese script:

- **`CI_JOB_TOKEN` no sirve** para escribir notas en la mayoría de las instalaciones de
  GitLab. Hay que crear un project access token con scope `api` y rol Reporter, y
  guardarlo como variable de CI enmascarada y protegida.
- **Si el comentario falla, el pipeline no falla.** Un token vencido no es razón para
  impedir un merge correcto: el veredicto ya lo entregan el código de salida y el widget.

### El veredicto va al final

Las tres primeras invocaciones usan `--fail-on none` a propósito. Si el gate cortara
antes, un MR con hallazgos se quedaría sin reporte y sin nota, que es exactamente cuando
más se necesitan.

### `interruptible` y `workflow:rules`

```yaml
default:
  interruptible: true

workflow:
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
    - when: never
```

Sin `interruptible`, cinco pushes seguidos dejan cinco pipelines compitiendo y la nota
del MR la escribe el que termine último, que no es necesariamente el del último commit.
Sin las `workflow:rules`, cada push a una rama dispara un pipeline duplicado además del
del merge request.

### Despliegue sin llaves

El job de deploy usa Workload Identity Federation: GitLab emite un JWT firmado
(`id_tokens`) y GCP lo canjea por credenciales de corta duración.

```yaml
id_tokens:
  GITLAB_OIDC_TOKEN:
    aud: https://gitlab.com
```

No hay llaves de service account en variables de CI, que es el secreto que más se filtra
y el que nadie rota. Configurar el pool de identidad es media hora de trabajo una sola
vez, y elimina de raíz la clase de incidente más común en pipelines de datos.

La promoción a producción es manual. No porque el gate no sea confiable, sino porque
promover es una decisión de negocio: hay ventanas, hay cierres de mes, y hay días en que
aunque el cambio sea correcto no es el momento.

## Adopción sin rebelión

Activar el gate en modo bloqueante sobre un warehouse heredado produce mil hallazgos y
un equipo que quiere apagarlo. La progresión que funciona:

| Semana | Configuración | Qué se busca |
|---|---|---|
| 1–2 | `--fail-on none` | medir, publicar la línea base |
| 3–4 | `--fail-on none`, umbral 0.6 | corregir lo evidente sin presión |
| 5–8 | `--fail-on block` sólo en gold y feature | proteger lo que se consume |
| 9+ | `--fail-on block` en silver, umbral 0.8 | régimen normal |

Y una regla que conviene explicitar desde el principio: **el gate sólo evalúa lo que el
PR toca.** Nadie debe quedar bloqueado por deuda que no introdujo. La deuda existente se
paga en un plan aparte, con su propio tiempo asignado.

## Verificación

```bash
make test       # 123 tests del motor y del enrutamiento de ingesta
make demo       # producto conforme: aprueba, exit 0
make demo-bad   # MR con violaciones: bloquea, exit 1
```

La demo con violaciones simula un merge request realista: una tabla
`CustomerContactList` en gold con correo y teléfono en claro, y la eliminación de una
columna que finanzas consume. Produce trece hallazgos bloqueantes con la corrección
concreta al lado de cada uno.

Vale la pena correrla en la primera reunión con un cliente. Es más convincente que
cualquier diagrama.

## Errores típicos

**Empezar con umbrales altos.** Garantiza el rechazo del equipo.

**Meter reglas de negocio en el gate.** "El monto no puede ser negativo" es una
aserción de Dataform, no una política de gobierno. El gate revisa metadatos y
estructura; los datos los revisan las aserciones.

**Olvidar la lista de siglas autorizadas.** Cada empresa tiene su jerga legítima. Sin
`allowed_acronyms`, el gate marca `sku`, `utm` y `gmv` como abreviaturas prohibidas y
pierde credibilidad en la primera corrida.

**Hacer que el LLM decida el bloqueo.** Vuelve el gate no determinista. Los equipos
aprenden a reintentar el pipeline en vez de corregir, y el control muere sin que nadie
lo apague formalmente.

---

Anterior: [Fase 3 · Silver y gold](05-fase-3-silver-y-gold.md) ·
Siguiente: [Fase 5 · Productos y contratos](07-fase-5-productos-y-contratos.md)
