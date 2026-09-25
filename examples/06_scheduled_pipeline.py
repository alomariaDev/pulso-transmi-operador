from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

from pulso_transmi import PulsoTransmiClient


BASE_URL = os.getenv("PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io").rstrip("/")
DATA_DIR = Path("data")


def load_env_file() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def current_cycle(api_key: str) -> dict | None:
    try:
        response = httpx.get(
            f"{BASE_URL}/v1/forecast-cycles/current",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )
        if response.status_code == 404:
            detail = response.json().get("detail", {})
            if detail == "no_open_cycle" or (
                isinstance(detail, dict) and detail.get("code") == "no_open_cycle"
            ):
                return None
        response.raise_for_status()
        cycle = response.json()
        if cycle.get("state") != "open":
            return None
        return cycle
    except Exception as e:
        print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Error al consultar ciclo: {e}")
        raise


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def process_cycle(cycle: dict, api_key: str) -> None:
    cycle_id = cycle.get("cycle_id", "desconocido")
    targets_count = len(cycle.get("targets", []))
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] 🚀 Ciclo abierto detectado: {cycle_id} ({targets_count} targets)")
    
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with PulsoTransmiClient(base_url=BASE_URL, api_key=api_key) as client:
        for filename in ("stations.csv", "observations.csv", "context.csv", "metadata.json"):
            client.download(filename, DATA_DIR / filename)
        stream = client.stream_observations_dataframe(
            end=cycle["data_cutoff"], released_by=cycle["opens_at"]
        )

    observations_path = DATA_DIR / "observations.csv"
    observations = pd.read_csv(
        observations_path, dtype={"station_id": "string"}, parse_dates=["observed_at"]
    )
    observations["observed_at"] = pd.to_datetime(observations["observed_at"], utc=True)
    cutoff = pd.to_datetime(cycle["data_cutoff"], utc=True)
    observations = observations.loc[observations["observed_at"] <= cutoff]
    combined = pd.concat([observations, stream], ignore_index=True)
    combined["observed_at"] = pd.to_datetime(combined["observed_at"], utc=True)
    combined["station_id"] = combined["station_id"].astype("string")
    combined = (
        combined.sort_values(["station_id", "observed_at"])
        .drop_duplicates(["station_id", "observed_at"], keep="last")
        .sort_values(["observed_at", "station_id"])
    )
    station_ids = {str(target["station_id"]) for target in cycle["targets"]}
    for station_id in station_ids:
        station_rows = combined.loc[combined["station_id"] == station_id, "observed_at"]
        if station_rows.empty or station_rows.max() != cutoff:
            raise RuntimeError(
                f"Los datos liberados para {station_id} no llegan al cutoff {cutoff.isoformat()}"
            )
    combined[["observed_at", "station_id", "demand"]].to_csv(
        observations_path, index=False
    )
    print("Datos sincronizados desde el API.")

    run([sys.executable, "examples/04_train_extra_trees.py"])
    run([sys.executable, "examples/05_submit_predictions.py", cycle_id])
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] ✅ Predicciones enviadas exitosamente para el ciclo {cycle_id}.")


def main() -> None:
    load_env_file()
    api_key = os.environ.get("PULSO_API_KEY")
    if not api_key:
        raise RuntimeError("PULSO_API_KEY no está configurada")

    cycle = current_cycle(api_key)
    if cycle is None:
        print("No hay ciclo abierto; la próxima ejecución programada volverá a consultar.")
        return

    process_cycle(cycle, api_key)
    print(
        f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
        f"✅ Predicciones enviadas para el ciclo {cycle['cycle_id']}."
    )


if __name__ == "__main__":
    main()
