# Supabase

La migración inicial crea el esquema `pulso` para persistir los datos del API,
features, cortes temporales, modelos, ejecuciones, predicciones y monitoreo.

## Aplicar en un proyecto nuevo

Requiere tener instalado Supabase CLI y haber creado el proyecto desde el
Dashboard de Supabase.

```bash
supabase login
supabase link --project-ref <PROJECT_REF>
supabase db push
```

La migración no incluye datos ni credenciales. El pipeline debe usar la clave
server-side (`service_role`) mediante secretos de GitHub; nunca debe exponerse
en el navegador.

## Exponer el esquema para la API

El esquema `pulso` queda protegido por RLS y no se habilita para acceso anónimo.
Si una aplicación necesita consultar sus tablas mediante PostgREST, agrega
`pulso` en **Project Settings > API > Exposed schemas** y crea políticas RLS
específicas para el rol correspondiente. El pipeline server-side puede usar el
`service_role`, que bypassa RLS.

## Entidades principales

- `stations`, `observations`, `context`: datos fuente del API.
- `data_cutoffs`, `feature_snapshots`: reproducibilidad y variables para ML.
- `model_versions`, `training_runs`, `predictions`: ciclo de entrenamiento y predicción.
- `data_quality_checks`, `drift_reports`: calidad, drift y decisiones de reentrenamiento.
- `prediction_evaluation`: vista para comparar predicciones contra observaciones disponibles.
