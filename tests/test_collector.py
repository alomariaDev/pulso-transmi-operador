import csv
import pytest

from pulso_transmi.collector import (
    STATION_COLUMNS,
    CONTEXT_COLUMNS,
    OBSERVATION_COLUMNS,
    coerce_value,
    rows_from_csv,
    collect,
)


def test_rows_from_csv_preserves_station_ids(tmp_path) -> None:
    path = tmp_path / "stations.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["station_id", "station_name"],
        )
        writer.writeheader()
        writer.writerow({"station_id": "02300", "station_name": "Calle 100"})

    rows = list(rows_from_csv(path, ("station_id", "station_name")))

    assert rows == [("02300", "Calle 100")]


def test_rows_from_csv_coerces_float_demand_to_integer(tmp_path) -> None:
    """Regression test: stream observations CSV may contain float strings like '141.0' and unreleased null/NaNs."""
    path = tmp_path / "stream_observations.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["observed_at", "station_id", "demand"],
        )
        writer.writeheader()
        writer.writerow({"observed_at": "2026-09-22T00:00:00Z", "station_id": "07107", "demand": "141.0"})
        writer.writerow({"observed_at": "2026-09-22T00:15:00Z", "station_id": "07107", "demand": "0.0"})
        writer.writerow({"observed_at": "2026-09-22T00:30:00Z", "station_id": "07107", "demand": "250.7"})
        writer.writerow({"observed_at": "2026-09-22T00:45:00Z", "station_id": "07107", "demand": "180"})
        writer.writerow({"observed_at": "2026-09-22T01:00:00Z", "station_id": "07107", "demand": ""})
        writer.writerow({"observed_at": "2026-09-22T01:15:00Z", "station_id": "07107", "demand": "nan"})

    rows = list(rows_from_csv(path, OBSERVATION_COLUMNS, table="observations"))

    assert rows == [
        ("2026-09-22T00:00:00Z", "07107", 141),
        ("2026-09-22T00:15:00Z", "07107", 0),
        ("2026-09-22T00:30:00Z", "07107", 251),
        ("2026-09-22T00:45:00Z", "07107", 180),
    ]
    # Verify exact types are integers
    assert all(isinstance(r[2], int) for r in rows)



def test_coerce_value_types() -> None:
    # Observations
    assert coerce_value("observations", "demand", "141.0") == 141
    assert coerce_value("observations", "demand", 141.0) == 141
    assert coerce_value("observations", "demand", "0") == 0
    assert coerce_value("observations", "demand", "") is None

    # Stations
    assert coerce_value("stations", "latitude", "4.6371") == 4.6371
    assert coerce_value("stations", "longitude", "-74.0793") == -74.0793
    assert coerce_value("stations", "station_id", "07107") == "07107"

    # Context
    assert coerce_value("context", "temperature_c", "14.5") == 14.5
    assert coerce_value("context", "rain_mm", "0.2") == 0.2
    assert coerce_value("context", "event_intensity", "0.5") == 0.5


def test_collector_error_messages_do_not_leak_secrets(tmp_path) -> None:
    """Ensure stage identification does not expose API keys or DB connection URLs."""
    secret_db_url = "postgresql://user:SUPER_SECRET_PASSWORD@db.example.com:5432/postgres"
    secret_api_key = "ptm_live_SUPER_SECRET_KEY"

    # Trigger with invalid API URL to test error wrapper
    with pytest.raises(RuntimeError) as exc_info:
        collect(
            api_url="https://invalid-non-existent-pulso-api.example.com",
            api_key=secret_api_key,
            database_url=secret_db_url,
            output_dir=tmp_path,
        )

    err_msg = str(exc_info.value)
    assert "[ETAPA: Descarga API]" in err_msg
    assert "SUPER_SECRET_PASSWORD" not in err_msg
    assert "SUPER_SECRET_KEY" not in err_msg
