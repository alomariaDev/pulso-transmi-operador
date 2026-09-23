import csv

from pulso_transmi.collector import rows_from_csv


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
