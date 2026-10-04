# Monitoreo y adaptación durante la fase de drift

## Qué observa el pipeline

`pulso-transmi-drift` corre cada hora. Calcula PSI con dos ventanas consecutivas
de siete días para la demanda global, la demanda de cada estación y el contexto
disponible. PSI igual o superior a 0,20 en cualquiera de esas señales activa el
reentrenamiento; los campos con cobertura insuficiente se declaran no
disponibles, no como drift confirmado. El reporte también incluye la edad del
dato más reciente de la API y marca como obsoletos datos con más de 36 horas.

El mismo workflow evalúa en Supabase las predicciones persistidas que ya tienen
observación real. Reporta WAPE, accuracy (`100 × max(0, 1 − WAPE)`) y cobertura:

- acumulado desde que existen predicciones en Supabase;
- las seis submissions/ciclos más recientes que sí están persistidos;
- cada uno de los horizontes 15, 30, 45 y 60 minutos;
- cada estación y horizonte para la ventana de seis ciclos.

La cobertura de etiquetas es la fracción de predicciones que ya tienen observación
real. La cobertura de submission compara targets esperados con filas guardadas.
Una submission aceptada puede seguir pendiente de evaluación: en ese caso se
conserva el conteo y la evaluación queda como `warning` hasta que se ingiera la
observación. El reporte no infiere ciclos oficiales sin submission; la ventana
reciente se refiere explícitamente a ciclos persistidos. Los fallos operativos
de Actions y los recibos de la API completan la evidencia de entregas.

## Cuándo entrenar y cómo conservar una versión

Cuando la API publica un ciclo abierto, `pulso-transmi-pipeline` entrena
ExtraTrees con observaciones disponibles hasta el `data_cutoff` de ese ciclo y
envía sus targets publicados. Cada Joblib queda versionado con SHA-256; la fila
de Supabase relaciona ese artefacto con ciclo, cutoff, rango de datos, filas,
parámetros y commit.

Una alerta PSI igual o superior a 0,20 dispara el reentrenamiento horario con los
datos liberados que la API entrega al monitor. El modelo, parámetros, rango de
datos, corte, commit, SHA-256 del Joblib y valores PSI se registran como un run
de MLflow (`pulso-transmi-operador`), usando la base de Supabase como backend de
tracking. El Joblib y el directorio de artefactos MLflow también se adjuntan a la
ejecución de GitHub Actions para conservar el binario aunque el runner sea
efímero. Solo se crea un run de drift por cada corte nuevo de observaciones. El
flujo normal sigue entrenando con el `data_cutoff` del ciclo abierto y, por
tanto, no usa observaciones posteriores para generar una submission.

La alerta PSI detecta cambios de distribución, no demuestra por sí sola que el
modelo nuevo mejore. El pipeline predice un cociente demanda futura / `lag_15m`
y lo escala por el lag conocido al origen, para que el target se adapte a
cambios de nivel por estación. HGB predice cada horizonte directamente; su vida
media se configura por horizonte. El target relativo mejoró en las tres
ventanas locales, incluyendo la más reciente, pero aún falta validarlo con
etiquetas posteriores al 21 sep. Clima y eventos se excluyen hasta recuperar
cobertura temporal consistente. El collector programado ha fallado, por lo que
se debe restablecer la ingestión antes de confiar en nuevos scores de drift.

En cuatro cortes temporales recientes (18 de septiembre, 17:00, 19:00, 21:00 y
22:00 UTC; 192 predicciones), el backtest obtuvo 62,73% con ExtraTrees recursivo
en los cuatro horizontes. La combinación desplegada obtuvo 67,53%: 84,10% y
70,36% en 15 y 30 minutos, y 58,29% y 55,99% en 45 y 60 minutos. Estos cortes
son una señal inicial de mejora, no evidencia suficiente para garantizar 80% en
el leaderboard. Se vigilan el accuracy oficial, la cobertura, cada horizonte y
los seis ciclos más recientes; la falta de etiquetas se reporta como pendiente,
no como accuracy cero. Ninguna submission ya aceptada se reescribe.

Una evaluación anterior con ocho cortes entre el 16 y el 17 de septiembre
encontró buenos resultados de HistGradientBoosting en horizontes largos. Esa
prueba no sustituye el benchmark actual de tres ventanas y no equivale al score
del leaderboard ni se debe sumar al acumulado oficial. La selección de modelos
vigente está documentada en [`model-comparison.md`](model-comparison.md).

La fase inicial seleccionó ExtraTrees frente a un baseline con una partición
temporal de siete días; esa evidencia está en [`model-comparison.md`](model-comparison.md).
Es una referencia inicial, no prueba de mejora durante drift. La evaluación
operativa que se agrega después de cada entrega usa resultados observados, nunca
datos posteriores al cutoff para producir predicciones de ese ciclo.

## Evidencia persistida

- `pulso.model_versions`: versión content-addressed del artefacto y parámetros.
- `pulso.data_cutoffs`: cutoff y rango temporal del entrenamiento.
- `pulso.training_runs`: ciclo, filas y métricas de evaluación por run.
- `pulso.predictions` y la vista `pulso.prediction_evaluation`: predicciones y
  errores cuando la observación real está disponible.
- `pulso.data_quality_checks`: PSI, accuracy por ventana/horizonte/estación y
  cobertura.
- MLflow: runs de entrenamiento y modelos asociados a corte, trigger y SHA-256.
- GitHub Actions: `drift_report.json`, `accuracy_report.json`, Joblib y artefactos
  MLflow por ejecución (retención de 90 días para el pipeline y 14 para drift).

El umbral PSI 0,20 es una alerta de monitoreo elegida por este proyecto; no es un
umbral impuesto por la organización del reto. Las decisiones se revisan durante
la fase y se documentan con resultados, cobertura, versiones y motivo.
