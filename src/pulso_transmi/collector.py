from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path
from typing import Iterable, Sequence

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


def rows_from_csv(path: Path, columns: Sequence[str]) -> Iterable[tuple[str, ...]]:
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            yield tuple(row[column] for column in columns)


def upsert_rows(
    connection: psycopg.Connection,
    table: str,
    columns: Sequence[str],
    rows: Iterable[tuple[str, ...]],
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
    batch: list[tuple[str, ...]] = []
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
) -> dict[str, int]:
    destination = output_dir or Path(tempfile.mkdtemp(prefix="pulso-collector-"))
    destination.mkdir(parents=True, exist_ok=True)
    files = ("stations.csv", "observations.csv", "context.csv")
    with PulsoTransmiClient(base_url=api_url, api_key=api_key, timeout=120.0) as client:
        for filename in files:
            client.download(filename, destination / filename)
        stream = client.stream_observations_dataframe()
    stream.to_csv(destination / "stream_observations.csv", index=False)

    with psycopg.connect(database_url, prepare_threshold=None) as connection:
        stations = upsert_rows(
            connection,
            "stations",
            STATION_COLUMNS,
            rows_from_csv(destination / "stations.csv", STATION_COLUMNS),
            ("station_id",),
            ("station_name", "corridor", "latitude", "longitude"),
        )
        context = upsert_rows(
            connection,
            "context",
            CONTEXT_COLUMNS,
            rows_from_csv(destination / "context.csv", CONTEXT_COLUMNS),
            ("observed_at",),
            tuple(column for column in CONTEXT_COLUMNS if column != "observed_at"),
        )
        observations = upsert_rows(
            connection,
            "observations",
            OBSERVATION_COLUMNS,
            rows_from_csv(destination / "observations.csv", OBSERVATION_COLUMNS),
            ("observed_at", "station_id"),
            ("demand",),
        )
        stream_observations = upsert_rows(
            connection,
            "observations",
            OBSERVATION_COLUMNS,
            rows_from_csv(
                destination / "stream_observations.csv", OBSERVATION_COLUMNS
            ),
            ("observed_at", "station_id"),
            ("demand",),
        )
        connection.commit()
    return {
        "stations": stations,
        "context": context,
        "observations": observations + stream_observations,
    }


def main() -> None:
    api_url = os.getenv("PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io")
    api_key = os.getenv("PULSO_API_KEY")
    database_url = os.getenv("SUPABASE_DB_URL", "").strip()
    if not api_key:
        raise RuntimeError("PULSO_API_KEY no está configurada")
    if not database_url:
        raise RuntimeError("SUPABASE_DB_URL no está configurada")

    counts = collect(api_url, api_key, database_url)
    print(
        "Datos sincronizados: "
        + ", ".join(f"{table}={count}" for table, count in counts.items())
    )


if __name__ == "__main__":
    main()
