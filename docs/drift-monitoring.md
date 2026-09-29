# Monitoreo y adaptación durante la fase de drift

## Qué observa el pipeline

`pulso-transmi-drift` corre cada hora. Calcula PSI con dos ventanas consecutivas
de siete días para la demanda y el contexto disponible. PSI igual o superior a
0,20 queda como alerta de distribución; los campos de contexto con cobertura
insuficiente se declaran no disponibles, no como drift confirmado.

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

Una alerta de PSI aislada solo inicia revisión; no dispara un entrenamiento
paralelo ni se presenta como motivo suficiente para cambiar de modelo. La Action
de drift no promueve artefactos: el artefacto de inferencia se genera dentro del
pipeline del ciclo y la versión queda sujeta a evaluación posterior. Se mantiene
la receta ExtraTrees actual hasta que una comparación temporal justifique cambiar
algoritmo o parámetros. Para proponer ese cambio, comparar la misma ventana de
validación y los mismos cuatro horizontes con coverage visible, revisar si el
error se concentra por estación/horizonte y considerar la tendencia de las seis
últimas entregas junto al acumulado. No decidir por un único ciclo malo ni
confundir ausencia de evaluación con accuracy cero.

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
- GitHub Actions: `drift_report.json`, `accuracy_report.json` y logs por ejecución.

El umbral PSI 0,20 es una alerta de monitoreo elegida por este proyecto; no es un
umbral impuesto por la organización del reto. Las decisiones se revisan durante
la fase y se documentan con resultados, cobertura, versiones y motivo.
