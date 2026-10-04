# Tests de integracion

Corren contra [Floci](https://floci.io), un emulador local de GCP. Se **saltan** cuando
el emulador no esta arriba, en vez de fallar: un test de integracion que pone rojo el
pipeline de quien no levanto la dependencia ensenia al equipo a ignorar el rojo.

```bash
make emulator           # levanta el emulador en el puerto 4588
make test-integration
make emulator-stop
```

| Archivo | Que verifica |
|---|---|
| `test_gcs_ingestion.py` | El comportamiento de la ingesta sobre Cloud Storage: cuarentena, motivo, borrado del original y no reentrada. |
| `test_bigquery_scan.py` | El escaneo de descubrimiento de la fase 0 contra la API de BigQuery: lectura de activos, tipos reales, labels y hallazgos del gate. |

## Lo que el emulador no cubre

| Servicio | Estado |
|---|---|
| Cloud Storage | emulacion completa |
| BigQuery | metadatos, inserts en streaming y un subconjunto de SQL |
| Data Catalog / policy tags | no soportado |
| Dataform | no soportado |
| Datastream | no soportado |

Consecuencias concretas para este repositorio:

- **Las transformaciones de silver y gold no se verifican aqui.** Necesitan un motor
  GoogleSQL real. Se prueban contra un proyecto GCP de desarrollo.
- **La carga a bronze tampoco**: no hay load jobs desde GCS. El test que la cubre esta
  marcado con `skip` y el motivo escrito, en lugar de fingir que pasa.
- **El particionado no se conserva**: `time_partitioning` vuelve como `None` al releer
  una tabla. No afecta a estos tests porque el gate no lo evalua, pero conviene saberlo
  antes de intentar verificar algo que dependa de ello.
