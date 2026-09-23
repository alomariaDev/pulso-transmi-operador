# Comparación inicial de modelos

La comparación se ejecutó sobre los datos descargados del API el 18 de septiembre
de 2026. La validación usa los últimos 7 días y el entrenamiento usa todo el
periodo anterior. No se mezclan aleatoriamente observaciones de futuro y pasado.

## Variables

- Rezagos de demanda de 15 minutos, 1 hora, 1 día y 7 días.
- Promedios móviles de 1 hora y 1 día.
- Desviación estándar móvil de 1 día.
- Hora, cuarto de hora, día de semana y fin de semana.
- Lluvia, pronóstico de lluvia, temperatura, pronóstico de temperatura e
  intensidad de eventos.
- Identificador de estación codificado como variable categórica.

Los rezagos y ventanas se calculan usando únicamente observaciones anteriores al
instante que se predice, para evitar data leakage.

## Resultado

| Modelo | WAPE | Accuracy |
|---|---:|---:|
| ExtraTreesRegressor | 0,1279 | 87,21% |
| HistGradientBoostingRegressor | 0,1298 | 87,02% |
| RandomForestRegressor | 0,1311 | 86,89% |
| Baseline estacional, lag de 1 día | 0,2100 | 79,00% |

El mejor resultado inicial es `ExtraTreesRegressor`, con una mejora relativa de
aproximadamente 39% en WAPE frente al baseline estacional.

## Decisión

Se selecciona `ExtraTreesRegressor` como candidato inicial para el pipeline. La
decisión todavía debe confirmarse con backtesting de varias ventanas temporales,
porque una única partición de 7 días no basta para medir estabilidad ante drift.

El experimento se puede repetir con:

```bash
python examples/03_model_comparison.py
```

El ranking se guarda en `artifacts/model_comparison.csv`, una carpeta ignorada
por Git para no versionar datasets ni artefactos generados localmente.

## Artefacto entrenado

El modelo ganador se entrenó con todas las filas disponibles después de crear
los rezagos y ventanas válidos: 43.776 filas y 17 features. El paquete Joblib
se genera con:

```bash
python examples/04_train_extra_trees.py
```

El archivo `artifacts/extra_trees_demand.joblib` contiene el estimador, el orden
de features, el mapeo de estaciones, los parámetros, el rango temporal de los
datos y el número de filas usadas. No se versiona porque es un artefacto binario
grande y debe regenerarse desde los datos versionados o descargados.

## Primera submission

El script `examples/05_submit_predictions.py` consultó el ciclo abierto, generó
una predicción por estación y envió las 12 predicciones con el modelo
`ExtraTreesRegressor`. El API aceptó oficialmente la submission:

- Ciclo: `cyc_practice_20260918`
- Submission: `sub_3f50d068a3df47b7966a5a58e28338c7`
- Estado: `accepted`
- Predicciones recibidas: 12 de 12
- Submission oficial: sí

La clave se lee desde `.env`, que está excluido por `.gitignore` y no forma parte
del repositorio.

## Operación automática

El workflow `.github/workflows/pulso-transmi-pipeline.yml` corre cada 10 minutos
y también puede ejecutarse manualmente. Cada ejecución:

1. consulta `GET /v1/forecast-cycles/current`;
2. termina en verde si recibe `404 no_open_cycle`;
3. descarga estaciones, observaciones, contexto y metadata solo con ciclo abierto;
4. entrena el modelo con los datos sincronizados;
5. envía el batch completo de targets con `Idempotency-Key` estable por ciclo;
6. conserva en los logs el recibo de la API, sin mostrar la clave.

El secreto `PULSO_API_KEY` se configura en **Settings > Secrets and variables >
Actions**. Las ejecuciones programadas de GitHub Actions usan el workflow de la
rama por defecto, por lo que esta rama debe integrarse a `main` para activar el
cron en producción.

## Próximas comprobaciones

- Repetir la validación con varias ventanas móviles.
- Reportar WAPE por estación, no solo el promedio global.
- Comparar entrenamiento global contra un modelo por estación.
- Ajustar hiperparámetros únicamente dentro del bloque de entrenamiento.
- Registrar la versión del modelo, features, cutoff y métricas en Supabase.
