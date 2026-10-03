# Comparación temporal de modelos
La comparación usa observaciones del API hasta el 20 de septiembre de 2026 y
tres ventanas temporales consecutivas de siete días (30 ago–6 sep, 6–13 sep y
13–20 sep). El entrenamiento de cada ventana termina antes de iniciar su
validación. El leaderboard se consultó el 3 de octubre; estas ventanas son más
recientes que el CSV versionado, pero todavía no cubren los ciclos posteriores
al 20 de septiembre.

## Variables
Los modelos usan rezagos causales, ventanas móviles, calendario y código de
estación. Se excluyen clima y eventos porque no están disponibles de forma
consistente hasta cada target. Las features usan solo observaciones anteriores
al origen; el test `test_target_demand_does_not_change_its_features` protege
contra leakage del valor objetivo.

## Resultado
Los scores agregan errores y demanda real de las tres ventanas antes de
calcular WAPE, igual que la métrica del leaderboard.

| Horizonte | Configuración HGB | Accuracy agregada | Ventana más reciente |
|---:|---|---:|---:|
| 15 min | absolute error, vida media 60 días | 78,30% | 66,25% |
| 30 min | squared error, vida media 60 días | 76,20% | 62,41% |
| 45 min | Poisson, vida media 30 días | 74,59% | 58,96% |
| 60 min | Poisson, vida media 60 días | 73,52% | 56,91% |

El baseline estacional de 24 horas alcanzó 69,60% en la ventana 13–20 sep,
superando los modelos en ese bloque de drift extremo; los modelos ganan al
agregar las tres ventanas. Una mezcla fija con ese baseline no mejoró el WAPE
agregado. La evaluación inicial de 87% usaba leakage y una sola partición, por
lo que no es una referencia válida.

## Decisión
El trainer usa HistGradientBoosting directo con `loss` y vida media ajustados
por horizonte en `examples/model_config.py`. El efecto más claro aparece a 15
minutos; Poisson aporta una mejora pequeña pero repetible a 45 y 60 minutos.
Esto no garantiza 90% ni mejora inmediata del leaderboard. El API no tiene
observaciones posteriores al 20 de septiembre, así que la siguiente evaluación
operativa depende de nuevos ciclos y etiquetas reales.

Repetir el benchmark temporal y la comparación con el baseline:
```bash
python examples/03_model_comparison.py
```

Los resultados por ventana se guardan en `artifacts/model_comparison.csv`.
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
