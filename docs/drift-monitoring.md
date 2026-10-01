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
efímero. El flujo normal sigue entrenando con el `data_cutoff` del ciclo abierto
y, por tanto, no usa observaciones posteriores para generar una submission.

La alerta PSI detecta cambios de distribución, no demuestra por sí sola que el
modelo nuevo mejore. El entrenamiento conserva la familia ExtraTrees y pondera
las observaciones con decaimiento exponencial y vida media de 14 días, para dar
mayor peso a patrones recientes sin desechar el historial. En cuatro cortes
temporales recursivos de una hora, esta variante obtuvo 84,46% frente a 84,15%
sin ponderación; subió en 15, 30 y 45 minutos y bajó 0,18 puntos en 60 minutos.
Por eso se conserva la receta y se monitorea cada horizonte. Estos resultados
offline no garantizan el leaderboard y no alteran retrospectivamente
submissions previas. La calidad se vigila con resultados oficiales y evaluación
persistida en Supabase. Cualquier cambio futuro requiere backtesting temporal,
cobertura visible y mejora consistente; considera también el error por estación
y las seis últimas entregas. No decidir por un único ciclo malo ni confundir
ausencia de evaluación con accuracy cero.

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
