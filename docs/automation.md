# Automatización y entregas: guía para estudiantes

El proyecto es individual. Cada estudiante opera su repositorio, su API key y,
si decide usarlo, su proyecto Supabase. El profesor publica datos y evalúa las
predicciones; el leaderboard se construye desde los recibos oficiales.

## El ciclo que debe automatizarse

```text
GitHub Actions
  → consulta el ciclo abierto y la entrega propia
  → ingiere los datos liberados
  → decide si conserva o reentrena el modelo
  → predice los targets exactos del ciclo
  → valida y envía una sola submission
  → guarda el recibo y monitorea el resultado posterior
```

`GET /v1/forecast-cycles/current` es la fuente del `cycle_id`, `data_cutoff`,
`closes_at` y de cada par `(station_id, target_at)` que hay que predecir. Un ciclo
oficial normal pide 12 estaciones × 4 horizontes = 48 valores. No calcules el
periodo desde la hora de GitHub ni escribas a mano las estaciones o fechas. Si
la API devuelve `404 no_open_cycle`, la ejecución puede terminar correctamente:
no hay entrega pendiente en ese momento.

Antes de entrenar, `GET /v1/submissions/current` con tu API key indica si ya
existe un recibo oficial tuyo para el ciclo abierto. Si lo hay, termina la
corrida; un nuevo payload válido con otra llave de idempotencia cuenta como
otro intento y reemplaza la entrega oficial. Repetir exactamente el mismo
payload con la misma llave devuelve el recibo anterior. Si responde
`404 no_submission_for_cycle`, prepara la
predicción. La API permite como máximo tres intentos aceptados por ciclo.

## Repositorio y secretos

1. Copia [`templates/pipeline.yml`](../templates/pipeline.yml) a
   `.github/workflows/pipeline.yml` de tu propio repositorio.
2. Implementa los módulos de ingesta, entrenamiento, predicción y monitoreo.
   La plantilla llama `python -m src.pipeline`; ese módulo debes crearlo tú.
3. En GitHub → Settings → Secrets and variables → Actions crea
   `PULSO_API_KEY` como **Secret**. Si usas Supabase, agrega también sus
   credenciales como Secrets. `PULSO_API_URL` puede ir en Variables.
4. Ejecuta primero **Run workflow** manualmente y comprueba en los logs el
   ciclo, el cutoff, la cantidad de targets y el recibo `accepted`.
5. Activa el `schedule` solo cuando tu código cierre bien los ciclos sin
   ventana, detecte recibos previos y registre errores sin revelar secretos.

Un éxito significa `status=accepted`, `predictions_received` igual a
`expected_predictions` y un `submission_id`. Conserva `X-Request-ID` y el
recibo para diagnosticar un rechazo. Nunca imprimas la API key, cédula ni
credenciales de Supabase en logs o artifacts.

## Modelo y datos entre corridas

Los runners de Actions son temporales. Si entrenas e infieres en la **misma**
corrida, puedes empezar sin almacenamiento de modelos: cada ejecución crea el
modelo desde datos disponibles hasta `data_cutoff` y genera el payload. Para
separar promoción e inferencia, guarda el artefacto de un modelo promovido en
un bucket **privado** de Supabase Storage y su metadata en Postgres: versión,
fecha/corte de entrenamiento, métricas de validación y ruta. La Action de
inferencia descarga esa versión y envía el resultado. Supabase también puede
guardar cursor de ingesta, observaciones nuevas, ejecuciones, recibos y métricas
de drift. Vercel corresponde al dashboard opcional; no es el lugar donde debe
entrenarse el modelo.

La validación del modelo debe ser temporal y anterior al cutoff. Decide cuándo
reentrenar a partir de evidencia de error y drift; esa decisión, las features y
el algoritmo son parte central de tu trabajo.

## Frecuencia y costo

La plantilla propone una consulta cada 15 minutos, en minutos 7, 22, 37 y 52
de cada hora UTC. La ventana de envío suele durar 25 minutos y el ciclo oficial
se abre aproximadamente cada hora; la API manda, porque el cron de GitHub puede
retrasarse. Un workflow sin ciclo abierto o ya entregado debe salir sin entrenar
ni hacer POST.

En el plan GitHub Free, los runners estándar de un repositorio privado tienen
una bolsa de **2.000 minutos mensuales por cuenta propietaria** y 500 MB de
artifacts. Cada job privado se redondea al minuto siguiente. Si una corrida
cuesta un minuto, 15 minutos de frecuencia durante 14 días son unas 1.344
corridas/minutos; deja margen para pruebas y otros workflows. Los repositorios
públicos tienen runners estándar gratuitos, pero su código queda visible.
Guarda en artifacts solo resultados necesarios y con retención corta; no subas
un modelo nuevo en cada consulta del reloj.

Supabase Free incluye 500 MB de Postgres y 1 GB de Storage por proyecto. Mide
el tamaño de tus tablas y conserva solo versiones promovidas del modelo. Sus
proyectos gratis pueden pausarse tras una semana de inactividad.

Vercel Hobby es gratuito para un dashboard personal. Úsalo para mostrar
accuracy, drift, estado del pipeline y leaderboard; el entrenamiento y la
entrega siguen en Actions. Los cron de Hobby no garantizan precisión temporal,
por lo que no deben controlar la ventana de submissions.

Fuentes oficiales consultadas el 23 de septiembre de 2026:
[GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions),
[minutos facturables](https://docs.github.com/en/actions/how-tos/monitor-workflows/view-job-execution-time),
[schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows),
[Supabase Free](https://supabase.com/docs/guides/platform/billing-on-supabase),
[Vercel Hobby](https://vercel.com/docs/plans/hobby),
[precisión de Vercel Cron](https://vercel.com/docs/cron-jobs/usage-and-pricing).

El [contrato técnico completo](https://github.com/uexternadojz/pulso-transmi/blob/main/docs/api-contract.md)
define el JSON, los guardrails, códigos de error e idempotencia.
