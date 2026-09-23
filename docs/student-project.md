# Guía del proyecto estudiantil

## Primera etapa: datos estáticos

1. instala el SDK y descarga el corte inicial;
2. crea tu Postgres en Supabase, estructura las tablas e importa el corte;
3. valida continuidad, duplicados, tipos y cobertura por estación;
4. realiza análisis exploratorio temporal y geográfico;
5. construye al menos dos baselines;
6. usa backtesting temporal y conserva evidencia de cada experimento;
7. define cómo versionarás modelo, features y cutoff.

## Segunda etapa: operación incremental

Cuando se active el reloj, GitHub Actions deberá:

1. consultar únicamente observaciones nuevas;
2. persistir el cursor o último timestamp procesado;
3. calcular métricas y señales de drift;
4. decidir si conserva o reentrena el modelo;
5. generar los cuatro horizontes solicitados;
6. enviar la predicción con versión y commit;
7. registrar éxito o error de la ejecución.

## Entregables mínimos

- repositorio reproducible;
- Postgres en Supabase con datos iniciales e incrementales;
- README con arquitectura y decisiones;
- pipeline automático en GitHub Actions;
- validación temporal y comparación contra baselines;
- monitoreo de datos y desempeño;
- estrategia explícita de reentrenamiento;
- historial de predicciones y modelos.

## Bono de dashboard

El dashboard puede mostrar:

- serie y mapa por estación;
- distribución de errores;
- accuracy acumulada y rolling 24h;
- señales de data/concept drift;
- última ejecución del pipeline;
- versión activa del modelo;
- posición en el leaderboard.

Vercel es opcional para el bono; Supabase y GitHub Actions hacen parte de la
arquitectura del proyecto. Nunca expongas claves privadas de Supabase en el
navegador.
