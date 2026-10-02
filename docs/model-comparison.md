# Comparación temporal de modelos
La comparación usa las observaciones locales disponibles hasta el 17 de
septiembre de 2026 y tres ventanas temporales consecutivas de siete días
(27 ago–3 sep, 3–10 sep y 10–17 sep). El entrenamiento de cada ventana termina
antes de iniciar su validación. El dataset local está desactualizado frente al
leaderboard consultado el 1 de octubre, por lo que estos resultados no estiman
el score actual de producción.
## Variables
Los modelos usan rezagos causales, ventanas móviles, calendario y código de
estación. Se excluyen clima y eventos porque su cobertura local termina el 8 de
septiembre y no está disponible de forma consistente para cada target. Las
features de demanda usan solamente observaciones anteriores al instante de
origen; los targets se desplazan según el horizonte. El test
`test_target_demand_does_not_change_its_features` protege contra leakage del
valor objetivo.
## Resultado
| Horizonte | Mejor modelo promedio | Accuracy promedio | Ventanas |
|---:|---|---:|---:|
| 15 min | HistGradientBoostingRegressor | 83,83% | 3 |
| 30 min | ExtraTreesRegressor | 83,23% | 3 |
| 45 min | HistGradientBoostingRegressor | 82,56% | 3 |
| 60 min | ExtraTreesRegressor | 82,21% | 3 |
En la ventana más reciente (10–17 sep), el accuracy varió entre 76,78% y
79,75%. El baseline estacional de 24 horas quedó por debajo de los modelos en
las tres ventanas. La evaluación anterior de 87% usaba una sola partición y
features que incluían demanda del mismo timestamp; no representa desempeño
reproducible y se retira como referencia válida.
## Decisión
El backtest identifica a HistGradientBoosting como ganador en 15 y 45 minutos y
a ExtraTrees en 30 y 60. Para producción se eligió HistGradientBoosting directo
en los cuatro horizontes: queda a menos de 0,15 puntos de accuracy de los
ganadores por horizonte, y evita un artefacto ExtraTrees superior a 1 GB. Se
usan pesos de recencia con vida media de 14 días. Esto es una mejora basada en
backtesting, no una garantía de 90% ni de aumento inmediato en el leaderboard;
hace falta reentrenar, enviar ciclos y esperar etiquetas reales para medir el
impacto operativo.
Repetir el experimento:
```bash
python examples/03_model_comparison.py
```
El ranking por ventana se guarda en `artifacts/model_comparison.csv`, una
carpeta ignorada por Git para no versionar datasets ni artefactos generados.
## Artefacto entrenado
El paquete Joblib se genera con:
```bash
python examples/04_train_extra_trees.py
```
El archivo `artifacts/extra_trees_demand.joblib` contiene los estimadores por
horizonte, el orden de features, el mapeo de estaciones, los parámetros, el
rango temporal de los datos y el número de filas usadas. No se versiona porque
es un artefacto binario grande y debe regenerarse desde los datos versionados o
descargados.
## Operación automática
El workflow `.github/workflows/pulso-transmi-pipeline.yml` corre cada 5 minutos
y también puede ejecutarse manualmente. Cuando no hay ciclo abierto, la ejecución
termina de inmediato; cada nuevo cron vuelve a consultar. Cada ejecución con
ciclo abierto:
1. consulta `GET /v1/forecast-cycles/current`;
2. termina en verde si recibe `404 no_open_cycle`;
3. descarga estaciones, contexto y metadata, y completa observaciones con el
   stream liberado hasta `data_cutoff`;
4. valida que cada serie llegue al cutoff sin huecos de 15 minutos y entrena el
   modelo con rezagos calculados sobre intervalos consecutivos;
5. envía el batch completo de targets con `Idempotency-Key` estable por ciclo;
6. conserva en los logs el recibo de la API, sin mostrar la clave.
El secreto `PULSO_API_KEY` se configura en **Settings > Secrets and variables >
Actions**. Las ejecuciones programadas usan el workflow de la rama por defecto,
por lo que esta rama debe integrarse a `main` para activar el cron en producción.
## Seguimiento
La estrategia de drift se documenta en [`drift-monitoring.md`](drift-monitoring.md).
Las submissions previas no se reescriben; cada nueva versión se identifica por
su hash, ciclo, cutoff, rango de datos, parámetros y commit. Se debe evaluar el
resultado oficial y los cuatro horizontes después de que lleguen etiquetas
reales. El siguiente objetivo experimental es acercarse al 90% sin validar
contra datos posteriores al cutoff ni usar features no disponibles al predecir.
