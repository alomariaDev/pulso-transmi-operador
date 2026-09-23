# Pulso TransMi — SDK para estudiantes

Starter kit oficial del reto MLOps **Pulso TransMi**. Incluye un cliente Python,
ejemplos reproducibles y una plantilla de GitHub Actions para construir un
pipeline que descargue datos, entrene, monitoree y envíe
predicciones.

> **Disponible públicamente:** la API de lectura está en
> `https://pulso-transmi.72-60-245-2.sslip.io` y su documentación interactiva en
> [`/docs`](https://pulso-transmi.72-60-245-2.sslip.io/docs).

## El reto

Se pronostica demanda sintética cada 15 minutos para 12 estaciones reales de
TransMilenio. El sistema liberará observaciones con el tiempo y cambiará algunos
patrones durante la competencia. Un modelo entrenado una sola vez puede perder
desempeño: el objetivo es operar un pipeline capaz de medir, decidir y
reentrenar.

La demanda, clima y eventos son sintéticos. Los nombres y coordenadas de las
estaciones provienen de datos oficiales de TransMilenio.

## Inicio rápido

Requiere Python 3.11 o superior.

```bash
git clone https://github.com/uexternadojz/pulso-transmi-sdk.git
cd pulso-transmi-sdk
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[ml]'
cp .env.example .env
python examples/01_download.py
python examples/02_naive_baseline.py
```

En Windows PowerShell, la activación es `.venv\Scripts\Activate.ps1`.

## Uso del SDK

```python
from pulso_transmi import PulsoTransmiClient

client = PulsoTransmiClient()

print(client.meta())
stations = client.stations()
observations = client.observations_dataframe(station_id="07107")
context = client.context_dataframe()

print(stations.head())
print(observations.tail())
```

El SDK recorre automáticamente todas las páginas. Si prefieres controlar cada
página, usa `client.observations_page(...)` y conserva `next_cursor` exactamente
como lo entrega la API.

## Datos iniciales

| Recurso | Tamaño |
|---|---:|
| Estaciones | 12 |
| Frecuencia | 15 minutos |
| Historia | 45 días |
| Periodos por estación | 4.320 |
| Observaciones | 51.840 |

Para evaluación local, usa una división temporal: por ejemplo, primeros 38 días
para entrenamiento y últimos 7 para validación. Una partición aleatoria mezcla
futuro y pasado y genera métricas engañosas.

## API y competencia `0.7.1`

| Método | Ruta | Uso |
|---|---|---|
| `GET` | `/health` | Estado básico |
| `GET` | `/v1/meta` | Versión, rango, hashes y enlaces |
| `GET` | `/v1/stations` | Catálogo geográfico |
| `GET` | `/v1/observations` | Demanda paginada |
| `GET` | `/v1/context` | Clima y eventos |
| `GET` | `/v1/downloads/{filename}` | Descarga completa |
| `GET` | `/v1/stream/observations` | Nuevos datos liberados, con cursor |
| `GET` | `/v1/forecast-cycles/current` | Ciclo abierto y objetivos exactos |
| `GET` | `/v1/submissions/current` | Recibo propio si ya entregaste el ciclo |
| `POST` | `/v1/submissions` | Entrega de predicciones con API key |

Swagger está disponible en `/docs`. Consulta [docs/api.md](docs/api.md) para
filtros, paginación y errores.

## Estructura esperada del proyecto individual

```text
mi-pulso-transmi/
├── src/
│   ├── ingest.py
│   ├── features.py
│   ├── train.py
│   ├── predict.py
│   └── monitor.py
├── tests/
├── artifacts/
├── requirements.txt o pyproject.toml
└── .github/workflows/pipeline.yml
```

El repositorio de cada estudiante debe dejar trazabilidad de:

- cutoff de datos usado;
- versión o commit del código;
- features y modelo entrenado;
- métricas de validación temporal;
- momento y razón de cada reentrenamiento;
- errores de ingesta o inferencia.

## GitHub Actions

[`templates/pipeline.yml`](templates/pipeline.yml) es una plantilla para el
repositorio individual. Cópiala a `.github/workflows/pipeline.yml`, implementa
`src/pipeline.py` y ejecuta primero `workflow_dispatch`. Luego agrega tu API key
como secret y activa el horario cuando el pipeline ya compruebe el ciclo y sus
recibos. El servidor solicita hasta 48 predicciones en cada ciclo oficial; lee
siempre los targets concretos de `/v1/forecast-cycles/current`.

Nunca escribas API keys, contraseñas de Supabase ni tokens dentro del código.
La guía [Automatización y entregas](docs/automation.md) explica el flujo,
la frecuencia, la evidencia de éxito y los límites gratuitos.

## Supabase y Vercel

Supabase es la base de datos del proyecto individual: conserva observaciones
incrementales, cursor de ingesta, ejecuciones, recibos y métricas. Su Storage
privado puede guardar versiones promovidas del modelo. Vercel es opcional y
corresponde al bono de visualización. La inferencia y automatización corren en
GitHub Actions.

Consulta [docs/student-project.md](docs/student-project.md) para el flujo completo
y los entregables.

## Métrica

La referencia actual es:

```text
WAPE = sum(abs(real - predicción)) / sum(real)
Accuracy = 100 × max(0, 1 - WAPE)
```

La métrica se calcula por estación y luego se promedia. Consulta el
[contrato vigente de submissions](https://github.com/uexternadojz/pulso-transmi/blob/main/docs/api-contract.md)
para el payload, los errores y la idempotencia.

## Desarrollo del SDK

```bash
python -m pip install -e '.[dev,ml]'
pytest -q
```

Este repositorio es público para estudiantes. No debe contener ground truth
futuro, semillas, configuración privada del escenario ni parámetros de drift.
