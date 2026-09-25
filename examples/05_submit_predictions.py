from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import joblib
import numpy as np
import pandas as pd

from model_features import build_features


BASE_URL = os.getenv("PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io").rstrip("/")
DATA_DIR = Path("data")
MODEL_PATH = Path("artifacts/extra_trees_demand.joblib")


def load_env_file() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def predict_targets(
    observations: pd.DataFrame,
    context: pd.DataFrame,
    targets: list[dict[str, object]],
    package: dict[str, object],
) -> list[dict[str, object]]:
    working_observations = observations.copy()
    working_context = context.copy()
    values_by_key: dict[tuple[str, str], float] = {}

    target_times = sorted({target["target_at"] for target in targets})
    for target_at_text in target_times:
        target_at = pd.Timestamp(target_at_text)
        targets_at = [target for target in targets if target["target_at"] == target_at_text]
        station_ids = [str(target["station_id"]) for target in targets_at]
        target_rows = pd.DataFrame(
            {"observed_at": [target_at] * len(station_ids), "station_id": station_ids, "demand": [np.nan] * len(station_ids)}
        )
        featured = build_features(
            pd.concat([working_observations, target_rows], ignore_index=True),
            working_context,
        )
        prediction_rows = featured.loc[
            (featured["observed_at"] == target_at) & featured["station_id"].isin(station_ids)
        ].copy()
        prediction_rows["station_code"] = prediction_rows["station_id"].map(package["station_codes"])
        prediction_rows = prediction_rows.set_index("station_id").loc[station_ids]
        values = np.maximum(
            package.get("prediction_floor", 0.0),
            package["model"].predict(prediction_rows[package["feature_columns"]]),
        )
        for station_id, value in zip(station_ids, values, strict=True):
            values_by_key[(station_id, target_at_text)] = round(float(value), 3)
        generated = target_rows.copy()
        generated["demand"] = values
        working_observations = pd.concat([working_observations, generated], ignore_index=True)

    predictions = [
        {
            "station_id": str(target["station_id"]),
            "target_at": target["target_at"],
            "value": values_by_key[(str(target["station_id"]), str(target["target_at"]))],
        }
        for target in targets
    ]
    expected_keys = [(str(target["station_id"]), str(target["target_at"])) for target in targets]
    if len(predictions) != len(targets) or len(set(expected_keys)) != len(expected_keys):
        raise RuntimeError("Los targets del ciclo contienen claves duplicadas o incompletas")
    return predictions


def main(expected_cycle_id: str | None = None) -> None:
    load_env_file()
    api_key = os.environ.get("PULSO_API_KEY")
    if not api_key:
        raise RuntimeError("PULSO_API_KEY no está configurada")

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=60.0) as client:
        cycle_response = client.get("/v1/forecast-cycles/current")
        cycle_response.raise_for_status()
        cycle = cycle_response.json()

        if expected_cycle_id is not None and cycle["cycle_id"] != expected_cycle_id:
            raise RuntimeError(
                "El ciclo actual cambió durante el procesamiento: "
                f"se esperaba {expected_cycle_id}, la API devolvió {cycle['cycle_id']}"
            )

        if cycle["state"] != "open":
            raise RuntimeError(f"El ciclo no está abierto: {cycle['state']}")

        package = joblib.load(MODEL_PATH)
        observations = pd.read_csv(
            DATA_DIR / "observations.csv",
            dtype={"station_id": "string"},
            parse_dates=["observed_at"],
        )
        context = pd.read_csv(DATA_DIR / "context.csv", parse_dates=["observed_at"])
        targets = cycle["targets"]
        if len(targets) != cycle["expected_predictions"]:
            raise RuntimeError("La API publicó una cantidad de targets inconsistente")
        predictions = predict_targets(observations, context, targets, package)
        client_run_id = f"extra-trees-{cycle['cycle_id']}"
        payload = {
            "schema_version": "1.0",
            "cycle_id": cycle["cycle_id"],
            "client_run_id": client_run_id,
            "data_cutoff": cycle["data_cutoff"],
            "model": {
                "version": "extra_trees_regressor_v1",
                "trained_at": datetime.fromtimestamp(MODEL_PATH.stat().st_mtime, timezone.utc).isoformat(),
                "training_data_end": package["data_end"],
                "git_commit": None,
            },
            "predictions": predictions,
        }
        response = client.post(
            "/v1/submissions",
            json=payload,
            headers={"Idempotency-Key": client_run_id},
        )
        if response.status_code == 409:
            detail = response.json().get("detail", {})
            if isinstance(detail, dict) and detail.get("code") == "idempotency_conflict":
                print(
                    json.dumps(
                        {
                            "cycle_id": cycle["cycle_id"],
                            "status": "already_submitted",
                            "message": "La API ya recibió una submission con esta clave de idempotencia; se conserva la aceptada.",
                        },
                        ensure_ascii=False,
                    )
                )
                return
        if response.status_code >= 400:
            raise RuntimeError(f"Submission rejected ({response.status_code}): {response.text}")
        response.raise_for_status()
        result = response.json()
        print(json.dumps({"cycle_id": cycle["cycle_id"], "submission": result, "prediction_count": len(predictions)}, indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 2:
        raise SystemExit("Uso: python examples/05_submit_predictions.py [cycle_id]")
    main(sys.argv[1] if len(sys.argv) == 2 else None)
