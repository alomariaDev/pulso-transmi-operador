---
name: guardar-predicciones
description: "Use when generating, validating, submitting, or persisting Pulso TransMi predictions. Covers API submissions, idempotency, model traceability, and storage in Supabase pulso.predictions."
---

# Guardar predicciones

Usa este flujo para producir predicciones reproducibles y conservar su trazabilidad.

## 1. Validar el ciclo

1. Leer `PULSO_API_URL` y `PULSO_API_KEY` desde `.env` o variables de entorno.
2. No imprimir, versionar ni copiar la API key a archivos de código.
3. Consultar `GET /v1/forecast-cycles/current` con `Authorization: Bearer <key>`.
4. Confirmar que el ciclo está `open`.
5. Usar exactamente su `cycle_id`, `data_cutoff`, estaciones y `target_at`.

## 2. Generar predicciones

1. Cargar el modelo Joblib desde `artifacts/`.
2. Construir las mismas features usadas durante el entrenamiento.
3. No usar observaciones posteriores a `data_cutoff`.
4. Generar una fila por cada target solicitado.
5. Validar antes del envío:
   - `station_id` conserva sus cinco dígitos.
   - `target_at` coincide exactamente con el ciclo.
   - `value` es finito y está entre 0 y 100000.
   - La cantidad coincide con `expected_predictions`.

## 3. Enviar al API

El payload de `POST /v1/submissions` debe incluir:

- `schema_version`: `"1.0"`.
- `cycle_id` del ciclo abierto.
- `client_run_id` único y reproducible.
- `data_cutoff` del ciclo.
- `model.version`, `trained_at`, `training_data_end` y `git_commit` cuando estén disponibles.
- `predictions` con `station_id`, `target_at` y `value`.

Enviar siempre el header `Idempotency-Key`, usando el mismo identificador de la
ejecución. Si el servidor responde `422`, leer el detalle y corregir el contrato
antes de reintentar. No crear un nuevo intento si el mismo `Idempotency-Key` ya
fue aceptado.

Guardar el `submission_id`, estado, cantidad recibida y hash del payload en el
registro de ejecución. Una respuesta válida debe tener `status: accepted`.

## 4. Persistir en Supabase

Registrar la ejecución en `pulso.training_runs`:

- `run_id`: igual a `client_run_id`.
- `model_id`: versión lógica del modelo.
- `cutoff_id`: identificador del corte usado.
- `status`: `succeeded` solo después de validar la respuesta.
- métricas y metadata en `metrics`.

Insertar cada predicción en `pulso.predictions`:

- `run_id` vincula la predicción con la ejecución.
- `station_id` referencia `pulso.stations`.
- `target_at` y `y_pred` contienen el resultado.
- `horizon` se expresa en minutos o en la convención acordada por el pipeline.
- `lower_bound` y `upper_bound` solo se rellenan si el modelo produce intervalos.

Usar la restricción única `(run_id, station_id, target_at, horizon)` para que los
reintentos sean idempotentes. La etiqueta real no se copia manualmente: se obtiene
uniendo `pulso.predictions` con `pulso.observations` por `station_id` y timestamp,
como hace la vista `pulso.prediction_evaluation`.

## 5. Verificar

Después de guardar:

1. Consultar la submission por `/v1/submissions/{submission_id}`.
2. Confirmar que las predicciones coinciden con el ciclo.
3. Confirmar el conteo de `pulso.predictions` para el `run_id`.
4. Ejecutar la vista `pulso.prediction_evaluation` cuando existan etiquetas reales.
5. Registrar errores de ingesta, generación o envío en `training_runs.error_message`.

Nunca expongas claves privadas de Supabase ni API keys en el navegador, logs,
commits o respuestas de usuario.
