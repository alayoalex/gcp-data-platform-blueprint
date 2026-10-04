# Referencia · Modelo de clasificación de datos

El catálogo completo vive en
[`governance/faro_governance/rules/privacy.yaml`](../governance/faro_governance/rules/privacy.yaml).

## Niveles de sensibilidad

| Nivel | Qué es | Quién lo lee |
|---|---|---|
| `PUBLIC` | Publicable sin restricción | cualquiera |
| `INTERNAL` | No es personal pero tampoco público | la organización |
| `CONFIDENTIAL` | Dato personal o información competitiva | grupo autorizado, con policy tag |
| `RESTRICTED` | Identificador directo, dato sensible o credencial | sólo el grupo de privacidad |

El orden es una escala, no una lista de etiquetas. El código lo hace explícito porque
`Sensitivity` hereda de `str`: sin operadores de orden propios, `INTERNAL >= CONFIDENTIAL`
resultaría `True` por comparación alfabética. Es un bug que existió durante la
construcción de este blueprint y que un test detectó. Hay una prueba de regresión.

## Categorías

| Categoría | Nivel | Policy tag | Ejemplos |
|---|---|---|---|
| `credential` | RESTRICTED | `security.credential` | `hashed_password`, `api_key`, `token` |
| `national_id` | RESTRICTED | `person.national_id` | `rut`, `dni`, `cpf`, `passport` |
| `payment_instrument` | RESTRICTED | `financial.payment_instrument` | `card_number`, `iban` |
| `special_category` | RESTRICTED | `person.special_category` | `medical_*`, `ethnicity`, `religion` |
| `contact` | CONFIDENTIAL | `person.contact` | `email`, `phone` |
| `person_name` | CONFIDENTIAL | `person.name` | `full_name`, `first_name` |
| `online_identifier` | CONFIDENTIAL | `person.online_identifier` | `ip_address`, `session_id`, `device_id` |
| `location` | CONFIDENTIAL | `person.location` | `address_line1`, `zip_code`, `latitude` |
| `person_reference` | CONFIDENTIAL | `person.reference` | `user_id`, `customer_id` |
| `demographic` | CONFIDENTIAL | `person.demographic` | `birth_date`, `gender` |
| `compensation` | CONFIDENTIAL | `financial.compensation` | `salary`, `income_amount` |
| `commercial` | INTERNAL | `commercial.internal` | `cost_amount`, `margin` |

### Tres categorías que conviene entender bien

**`credential`** está prohibida en las cuatro capas, no restringida. No es un dato que
haya que proteger: es un dato que no debe salir del sistema transaccional, porque no
tiene ningún uso analítico.

**`person_reference`** es la menos obvia y la más importante. Una llave foránea a la
tabla de personas no es PII por sí sola, pero convierte cualquier tabla que la contenga
en un dato personal indirecto: basta un join. Por eso la política exige seudonimizarla
al cruzar a silver, y por eso `mst_customer` usa `customer_key` y no `user_id`.

**`special_category`** corresponde a los datos sensibles del Art. 2 letra g) de la Ley
21.719. Genera siempre un hallazgo bloqueante, incluso si está correctamente etiquetada:
requiere base de licitud reforzada y aprobación explícita, no sólo un policy tag.

## El orden del catálogo importa

Gana la primera regla que calza. Las específicas van arriba.

Ejemplo real: `ip_address` calzaba con `(^|_)address($|_)` de la categoría `location`,
porque `location` estaba antes que `online_identifier`. El test lo detectó y la
corrección fue reordenar el catálogo, no cambiar el patrón.

## Matriz de tratamiento

Qué se exige según el nivel y la capa:

| | BRONZE | SILVER | GOLD | FEATURE |
|---|---|---|---|---|
| `RESTRICTED` | permitido | seudonimizar | prohibido | prohibido |
| `CONFIDENTIAL` | permitido | enmascarar | prohibido | seudonimizar |
| `INTERNAL` | permitido | permitido | permitido | permitido |
| `PUBLIC` | permitido | permitido | permitido | permitido |

Bronze permite todo porque es la copia fiel del origen y su control es de acceso, no de
forma: sólo el service account del producto lo lee.

### La excepción de la bóveda

Una tabla con la label `data_vault: "true"` queda exenta de la exigencia de
seudonimización o enmascaramiento, **siempre que toda columna clasificada lleve su
policy tag**.

La exención tiene sentido porque una bóveda existe para concentrar el dato
identificable: seudonimizarla la dejaría sin propósito. Y se paga con etiquetado —el
control se traslada de la forma del dato al permiso de lectura. Una bóveda sin etiquetar
no obtiene la exención; es simplemente una tabla con datos personales.

## Exclusiones: evitar falsos positivos

```yaml
exclusions:
  - '_domain_name$'   # `email_domain_name` ES la mitigación, no el riesgo
  - '_domain$'
  - '_flag$'          # `has_email_flag` es una preferencia, no un correo
  - '_count$'         # un contador de correos no es un correo
  - '_quantity$'
  - '_percentage$'
  - '^has_'
  - '^is_'
  - '_hash_code$'
  - '_surrogate_key$'
```

Las exclusiones no son concesiones al rigor: son lo que mantiene vivo el control. Un
gate que marca `email_count` como dato de contacto pierde la confianza del equipo en una
semana, y un gate en el que nadie confía se desactiva.

Mantener esta lista es trabajo continuo. Cada falso positivo que un equipo reporta y que
no se corrige es un paso hacia que dejen de reportarlos.

## Confianza y contexto

La confianza base es 0.75, y se ajusta:

- **+0.15** si el dataset contiene una palabra de contexto personal (`customer`, `user`,
  `employee`, `patient`, `hr`, `payroll`).
- **+0.15** si la tabla la contiene.
- **Mínimo 0.90** para los identificadores fuertes, sin importar el contexto: un RUT es
  un RUT en cualquier tabla.

Por debajo de 0.70, el hallazgo se marca para revisión humana en lugar de decidirse
automáticamente.

El ejemplo que justifica todo el mecanismo: una columna `full_name` en
`customer_silver.mst_customer` es casi con certeza el nombre de una persona; la misma
columna en `catalog_silver.mst_product` es el nombre de un producto.

## Marco legal

| Norma | Qué aporta al modelo |
|---|---|
| Ley 21.719 (Chile) | Define datos personales y sensibles; exige base de licitud, y derechos ARCO con plazos |
| GDPR Art. 4(1) | Los identificadores en línea son datos personales aunque no lleven nombre |
| GDPR Art. 9 | Categorías especiales; base del mapeo de `special_category` |
| PCI-DSS | Alcance de `payment_instrument` |

La correspondencia entre el catálogo y estas normas no es automática ni completa. Es un
punto de partida razonable que un abogado debe revisar para cada cliente, y conviene
decirlo así en las propuestas.

---

Volver al [índice](../README.md)
