from __future__ import annotations

import csv
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

import psycopg

from .client import PulsoTransmiClient


STATION_COLUMNS = ("station_id", "station_name", "corridor", "latitude", "longitude")
CONTEXT_COLUMNS = (
    "observed_at",
    "rain_mm",
    "rain_forecast",
    "temperature_c",
    "temperature_forecast",
    "event_intensity",
)
OBSERVATION_COLUMNS = ("observed_at", "station_id", "demand")


def coerce_value(table: str, column: str, value: Any) -> Any:
    if value is None or value == "" or str(value).lower() in ("nan", "null", "none"):
        return None
    if table == "observations" and column == "demand":
        # Coerce float strings (e.g., "141.0") or floats/ints to integer for PostgreSQL
        return int(round(float(value)))
    if table == "context" and column in (
        "rain_mm",
        "rain_forecast",
        "temperature_c",
        "temperature_forecast",
        "event_intensity",
    ):
        return float(value)
    if table == "stations" and column in ("latitude", "longitude"):
        return float(value)
    return str(value)


def rows_from_csv(
    path: Path,
    columns: Sequence[str],
    table: str | None = None,
) -> Iterable[tuple[Any, ...]]:
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if table is not None:
                if table == "observations":
                    val = row.get("demand")
                    if val is None or val == "" or str(val).lower() in ("nan", "null", "none"):
                        continue
                yield tuple(coerce_value(table, col, row.get(col)) for col in columns)
            else:
                yield tuple(row[column] for column in columns)


def upsert_rows(
    connection: psycopg.Connection,
    table: str,
    columns: Sequence[str],
    rows: Iterable[tuple[Any, ...]],
    conflict_columns: Sequence[str],
    update_columns: Sequence[str],
    batch_size: int = 1000,
) -> int:
    column_sql = ", ".join(columns)
    conflict_sql = ", ".join(conflict_columns)
    update_sql = ", ".join(f"{column} = excluded.{column}" for column in update_columns)
    statement = (
        f"insert into pulso.{table} ({column_sql}) values ({', '.join(['%s'] * len(columns))}) "
        f"on conflict ({conflict_sql}) do update set {update_sql}"
    )
    total = 0
    batch: list[tuple[Any, ...]] = []
    with connection.cursor() as cursor:
        for row in rows:
            batch.append(row)
            if len(batch) >= batch_size:
                cursor.executemany(statement, batch)
                total += len(batch)
                batch.clear()
        if batch:
            cursor.executemany(statement, batch)
            total += len(batch)
    return total


def collect(
    api_url: str,
    api_key: str,
    database_url: str,
    output_dir: Path | None = None,
    max_retries: int = 3,
) -> dict[str, int]:
    destination = output_dir or Path(tempfile.mkdtemp(prefix="pulso-collector-"))
    destination.mkdir(parents=True, exist_ok=True)
    files = ("stations.csv", "observations.csv", "context.csv")

    # Etapa 1: Descarga desde API
    try:
        with PulsoTransmiClient(base_url=api_url, api_key=api_key, timeout=120.0) as client:
            for filename in files:
                client.download(filename, destination / filename)
            stream = client.stream_observations_dataframe()
        if not stream.empty and "demand" in stream.columns:
            stream = stream.dropna(subset=["demand"])
        stream.to_csv(destination / "stream_observations.csv", index=False)
    except Exception as exc:
        raise RuntimeError(f"[ETAPA: Descarga API] Fallo al obtener datos del API: {exc}") from None

    # Etapa 2: Ingesta y Persistencia en Supabase PostgreSQL con reintentos para fallos transitorios
    for attempt in range(1, max_retries + 1):
        try:
            with psycopg.connect(database_url, prepare_threshold=None) as connection:
                try:
                    stations = upsert_rows(
                        connection,
                        "stations",
                        STATION_COLUMNS,
                        rows_from_csv(destination / "stations.csv", STATION_COLUMNS, table="stations"),
                        ("station_id",),
                        ("station_name", "corridor", "latitude", "longitude"),
                    )
                except Exception as exc:
                    raise RuntimeError(f"[ETAPA: Ingesta Stations] Error insertando estaciones: {type(exc).__name__}: {exc}") from None

                try:
                    context = upsert_rows(
                        connection,
                        "context",
                        CONTEXT_COLUMNS,
                        rows_from_csv(destination / "context.csv", CONTEXT_COLUMNS, table="context"),
                        ("observed_at",),
                        tuple(column for column in CONTEXT_COLUMNS if column != "observed_at"),
                    )
                except Exception as exc:
                    raise RuntimeError(f"[ETAPA: Ingesta Context] Error insertando contexto: {type(exc).__name__}: {exc}") from None

                try:
                    observations = upsert_rows(
                        connection,
                        "observations",
                        OBSERVATION_COLUMNS,
                        rows_from_csv(destination / "observations.csv", OBSERVATION_COLUMNS, table="observations"),
                        ("observed_at", "station_id"),
                        ("demand",),
                    )
                except Exception as exc:
                    raise RuntimeError(f"[ETAPA: Ingesta Observations] Error insertando observaciones históricas: {type(exc).__name__}: {exc}") from None

                try:
                    stream_observations = upsert_rows(
                        connection,
                        "observations",
                        OBSERVATION_COLUMNS,
                        rows_from_csv(
                            destination / "stream_observations.csv", OBSERVATION_COLUMNS, table="observations"
                        ),
                        ("observed_at", "station_id"),
                        ("demand",),
                    )
                except Exception as exc:
                    raise RuntimeError(f"[ETAPA: Ingesta Stream Observations] Error insertando observaciones de stream: {type(exc).__name__}: {exc}") from None

                connection.commit()
                return {
                    "stations": stations,
                    "context": context,
                    "observations": observations + stream_observations,
                }
        except psycopg.OperationalError as exc:
            if attempt == max_retries:
                raise RuntimeError(
                    f"[ETAPA: Conexión BD] Fallo persistente de conexión a PostgreSQL tras {max_retries} intentos: {type(exc).__name__}"
                ) from None
            time.sleep(2**attempt)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"[ETAPA: Persistencia BD] Error inesperado en base de datos: {type(exc).__name__}: {exc}") from None

    raise AssertionError("unreachable")


def load_env_file() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def main() -> None:
    load_env_file()
    api_url = os.getenv("PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io")
    api_key = os.getenv("PULSO_API_KEY")
    database_url = os.getenv("SUPABASE_DB_URL", "").strip()
    if not api_key:
        raise RuntimeError("[ETAPA: Configuración] PULSO_API_KEY no está configurada")
    if not database_url:
        raise RuntimeError("[ETAPA: Configuración] SUPABASE_DB_URL no está configurada")

    counts = collect(api_url, api_key, database_url)
    print(
        "Datos sincronizados: "
        + ", ".join(f"{table}={count}" for table, count in counts.items())
    )


if __name__ == "__main__":
    main()
