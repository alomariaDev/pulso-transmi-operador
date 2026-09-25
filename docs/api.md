# API de lectura

La URL base pública es:

```text
https://pulso-transmi.72-60-245-2.sslip.io
```

## Paginación

`/v1/observations` y `/v1/context` devuelven como máximo 5.000 filas. Una
respuesta incluye `data`, `count` y `next_cursor`. Envía el cursor sin
modificarlo en la solicitud siguiente. `null` indica el final.

```python
cursor = None
while True:
    page = client.observations_page(cursor=cursor, limit=5000)
    process(page["data"])
    cursor = page["next_cursor"]
    if cursor is None:
        break
```

## Filtros

```text
GET /v1/observations?station_id=07107
GET /v1/observations?start=2026-08-01T00:00:00-05:00&limit=5000
GET /v1/context?start=2026-08-01T00:00:00-05:00
```

`start` y `end` son inclusivos y deben incluir zona horaria. Los IDs de estación
son texto: no elimines sus ceros iniciales.

## Stream de competencia

`GET /v1/stream/observations` devuelve observaciones liberadas por la competencia,
con `observed_at`, `station_id`, `demand` y `released_at`. Recorre todas las
páginas con `next_cursor`; el cursor es opaco y debe conservarse sin cambios.
La respuesta también informa `server_time`. Para una predicción, filtra por el
`data_cutoff` del ciclo y excluye registros cuyo `released_at` sea posterior a
`opens_at`; así no se usan observaciones reveladas después de abrir el ciclo.
Este stream completa el dataset inicial, cuyo archivo de descarga no incluye las
observaciones futuras que luego libera la competencia.

## Descargas completas

```text
GET /v1/downloads/stations.csv
GET /v1/downloads/observations.csv
GET /v1/downloads/context.csv
GET /v1/stream/observations?limit=5000
GET /v1/downloads/metadata.json
```

El SDK verifica automáticamente el SHA-256 declarado en `/v1/meta`.

## Errores

| Código | Significado |
|---:|---|
| 400 | Cursor inválido |
| 404 | Archivo o ruta inexistente |
| 422 | Parámetro inválido |
| 429 | Demasiadas solicitudes; espera antes de reintentar |
| 5xx | Falla temporal del servidor |
