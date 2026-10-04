# ADR-0004 · Concentrar el dato personal en una bóveda en vez de eliminarlo

**Estado:** aceptada

## Contexto

Hay dos formas de manejar datos personales en un warehouse analítico: eliminarlos del
todo, o concentrarlos. La primera suena más segura.

## Decisión

Concentrarlos. `mst_customer_identity` es la única tabla con correo, nombre, teléfono y
dirección; lleva policy tags por columna y su propia lista de acceso. Todo lo demás usa
el seudónimo `customer_key`.

## Razones

Eliminar el dato personal es **imposible**: hay obligaciones legales que exigen poder
responder qué datos se tienen de una persona. Y es **contraproducente**: lo que ocurre
en la práctica es que alguien lo vuelve a traer por un camino lateral —un export, una
tabla en sandbox, un join en un notebook— y entonces está en el warehouse sin control
alguno.

Lo que la concentración compra:

- El control de acceso se audita mirando una tabla, no el warehouse completo.
- Una solicitud de acceso o supresión se resuelve con una consulta acotada.
- Al borrar a una persona, los seudónimos que quedaron en las tablas analíticas dejan de
  ser reconectables. Los agregados históricos sobreviven; la identidad no.

## Consecuencias

- Requiere una sal secreta y estable, con procedimiento de rotación escrito. Es el
  procedimiento más caro de la plataforma y hay que tenerlo antes de necesitarlo.
- El dato seudonimizado **sigue siendo dato personal** bajo la Ley 21.719. La bóveda
  reduce el riesgo; no saca a la plataforma del alcance de la norma.
- El motor de gobierno necesita reconocer el patrón para no exigir seudonimización sobre
  la bóveda misma. Se resuelve con la label `data_vault` y la exigencia de que toda
  columna clasificada lleve policy tag.
