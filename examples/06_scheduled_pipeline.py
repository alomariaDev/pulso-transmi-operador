from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import httpx

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
        raise RuntimeError(f"El ciclo actual no está abierto: {cycle.get('state')}")
    return cycle


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def main() -> None:
    load_env_file()
    api_key = os.environ.get("PULSO_API_KEY")
    if not api_key:
        raise RuntimeError("PULSO_API_KEY no está configurada")

    cycle = current_cycle(api_key)
    if cycle is None:
        print("No hay ciclo abierto; el workflow termina correctamente.")
        return

    print(f"Ciclo abierto: {cycle['cycle_id']} ({len(cycle['targets'])} targets)")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with PulsoTransmiClient(base_url=BASE_URL, api_key=api_key) as client:
        for filename in ("stations.csv", "observations.csv", "context.csv", "metadata.json"):
            client.download(filename, DATA_DIR / filename)
    print("Datos sincronizados desde el API.")

    run([sys.executable, "examples/04_train_extra_trees.py"])
    run([sys.executable, "examples/05_submit_predictions.py"])


if __name__ == "__main__":
    main()
