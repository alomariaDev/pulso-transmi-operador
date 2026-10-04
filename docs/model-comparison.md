# Comparación temporal de modelos
El dataset versionado llega al 17 de septiembre de 2026. El snapshot público del
4 de octubre recibió observaciones del API hasta el 21 de septiembre, pero la
API y el collector dejan varios días sin datos etiquetados. El backtest
reproducible usa tres ventanas de siete días hasta el 17 de septiembre: 27 ago–3
sep, 3–10 sep y 10–17 sep. Cada modelo se entrena solo con datos anteriores a
su ventana de validación.

## Variables
Los modelos usan rezagos causales, ventanas móviles, calendario y código de
estación. Se excluyen clima y eventos porque no están disponibles de forma
consistente hasta cada target. Las features usan solo observaciones anteriores
al origen; el test `test_target_demand_does_not_change_its_features` protege
contra leakage del valor objetivo.

## Resultado
WAPE agrega errores absolutos y demanda real de las tres ventanas, igual que la
métrica oficial. V5 predice `target / lag_15m` y escala el cociente por el último
lag disponible; V4 predecía la demanda absoluta.

| Horizonte | V4 absoluto | V5 relativo | Mejora WAPE→accuracy |
|---:|---:|---:|---:|
| 15 min | 83,93% | 85,23% | +1,30 pp |
| 30 min | 83,17% | 84,45% | +1,28 pp |
| 45 min | 82,45% | 83,36% | +0,91 pp |
| 60 min | 81,98% | 82,58% | +0,60 pp |

En la ventana 10–17 sep la mejora fue de 2,1–3,3 puntos. La PSI de demanda
global del último snapshot fue 0,063, pero no hay cobertura suficiente para
calcular PSI por estación ni para clima/eventos. Los últimos cortes etiquetados
siguen limitados y el snapshot oficial del 4 de octubre marcó 66,74% acumulado,
puesto 13, y 42,51% en 24 h, puesto 17. Estos son scores oficiales previos a
validar v5, no resultados de esta mejora. La evaluación inicial de 87% usaba
leakage y una sola partición, y se descarta como referencia válida.

## Decisión
La mejora es consistente en tres ventanas, pero aún es offline. V5 usa HGB
directo con target relativo y las vidas medias en `examples/model_config.py`.
No se limita el cociente porque los límites probados redujeron el WAPE a 45/60
min. El API no da datos posteriores al 21 sep y el collector programado falló
en las últimas ejecuciones; primero hay que recuperar ingestión y luego medir
v5 con etiquetas nuevas. La mejora no garantiza 90% ni se debe sumar al score
oficial hasta tener evaluación real.

Repetir el benchmark temporal y la comparación con v4:
```bash
python examples/03_model_comparison.py
```

Los resultados por ventana se guardan en `artifacts/model_comparison.csv`.
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
