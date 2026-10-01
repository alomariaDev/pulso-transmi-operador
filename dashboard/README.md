# Dashboard MLOps de Pulso TransMi

Dashboard adaptable para móvil y escritorio que cubre los elementos del bono de
visualización del proyecto: mapa y serie temporal por estación, distribución de
errores, accuracy oficial acumulada y de 24 horas, drift PSI, última ejecución,
modelo de la última submission y puesto en el leaderboard.

El navegador solo descarga `data/summary_data.json`, un agregado público. Nunca
recibe `PULSO_API_KEY`, la conexión a Supabase ni otras credenciales. La UI
muestra antigüedad del corte y cobertura; una submission sin etiquetas no se
presenta como un resultado evaluado.

## Datos y ejecución local

Requiere Python 3.11+, `PULSO_API_KEY` y `SUPABASE_DB_URL`. El generador lee esas
variables del entorno o de `.env` en la raíz del repositorio (también busca el
`.env` del SDK hermano durante desarrollo local).

```bash
python -m pip install -e .
python dashboard/build_data.py
python -m http.server 8000 --directory dashboard
```

Abre `http://localhost:8000`. El generador consulta el API de Pulso TransMi y
el leaderboard oficial, evalúa predicciones y versiones en Supabase y lee el
último estado de GitHub Actions. No fabrica predicciones para completar las
gráficas.

## Actualización automática

`.github/workflows/dashboard-data.yml` regenera el snapshot cada hora con los
secrets ya configurados para el pipeline y el token temporal de GitHub Actions.
Solo sube el JSON agregado. Si cambió, guarda una actualización en Git; una
integración existente con Vercel despliega el cambio al recibir ese commit.
También se puede ejecutar desde **Actions → pulso-transmi-dashboard → Run
workflow**.

Para alojar en Vercel, selecciona `dashboard` como Root Directory. El proyecto
es estático: no requiere variables privadas ni funciones serverless en Vercel.
`vercel.json` evita servir una versión cacheada del snapshot recién actualizado.

El dashboard puede ejecutarse y previsualizarse sin Vercel. El workflow solo
actualiza los datos versionados; no habilita una cuenta o dominio de hosting.
