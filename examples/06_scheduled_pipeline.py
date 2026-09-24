from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from pulso_transmi import PulsoTransmiClient


BASE_URL = os.getenv("PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io").rstrip("/")
DATA_DIR = Path("data")
MAX_POLL_SECONDS = int(os.getenv("MAX_POLL_SECONDS", "720"))  # 12 minutos por ejecución
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "25"))  # Consulta cada 25 segundos


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
        return None


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
    print("Datos sincronizados desde el API.")

    run([sys.executable, "examples/04_train_extra_trees.py"])
    run([sys.executable, "examples/05_submit_predictions.py"])
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] ✅ Predicciones enviadas exitosamente para el ciclo {cycle_id}.")


def main() -> None:
    load_env_file()
    api_key = os.environ.get("PULSO_API_KEY")
    if not api_key:
        raise RuntimeError("PULSO_API_KEY no está configurada")

    start_time = time.time()
    processed_cycles = set()
    print(f"Iniciando escucha activa de ciclos por hasta {MAX_POLL_SECONDS}s (intervalo: {POLL_INTERVAL_SECONDS}s)...")

    while time.time() - start_time < MAX_POLL_SECONDS:
        cycle = current_cycle(api_key)
        if cycle is not None and cycle.get("cycle_id") not in processed_cycles:
            process_cycle(cycle, api_key)
            processed_cycles.add(cycle["cycle_id"])
        else:
            remaining = int(MAX_POLL_SECONDS - (time.time() - start_time))
            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Sin ciclo abierto nuevo. Esperando {POLL_INTERVAL_SECONDS}s (restan ~{remaining}s de ventana)...")

        if time.time() - start_time + POLL_INTERVAL_SECONDS >= MAX_POLL_SECONDS:
            break
        time.sleep(POLL_INTERVAL_SECONDS)

    print("Ventana de polling completada correctamente.")


if __name__ == "__main__":
    main()
