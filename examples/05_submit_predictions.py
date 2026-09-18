from __future__ import annotations

import json
import os
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


def main() -> None:
    load_env_file()
    api_key = os.environ.get("PULSO_API_KEY")
    if not api_key:
        raise RuntimeError("PULSO_API_KEY no está configurada")

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=60.0) as client:
        cycle_response = client.get("/v1/forecast-cycles/current")
        cycle_response.raise_for_status()
        cycle = cycle_response.json()

        if cycle["state"] != "open":
            raise RuntimeError(f"El ciclo no está abierto: {cycle['state']}")

        package = joblib.load(MODEL_PATH)
        observations = pd.read_csv(
            DATA_DIR / "observations.csv",
            dtype={"station_id": "string"},
            parse_dates=["observed_at"],
        )
        context = pd.read_csv(DATA_DIR / "context.csv", parse_dates=["observed_at"])
        target_at = pd.Timestamp(cycle["targets"][0]["target_at"])
        station_ids = [target["station_id"] for target in cycle["targets"]]

        latest_context = context.sort_values("observed_at").iloc[-1].copy()
        target_context = pd.DataFrame(
            [{"observed_at": target_at, **{column: latest_context[column] for column in context.columns if column != "observed_at"}}]
        )
        context_for_prediction = pd.concat([context, target_context], ignore_index=True)
        target_rows = pd.DataFrame(
            {"observed_at": [target_at] * len(station_ids), "station_id": station_ids, "demand": [np.nan] * len(station_ids)}
        )
        featured = build_features(pd.concat([observations, target_rows], ignore_index=True), context_for_prediction)
        prediction_rows = featured.loc[
            (featured["observed_at"] == target_at) & featured["station_id"].isin(station_ids)
        ].copy()
        prediction_rows["station_code"] = prediction_rows["station_id"].map(package["station_codes"])
        prediction_rows = prediction_rows.set_index("station_id").loc[station_ids]
        values = package["model"].predict(prediction_rows[package["feature_columns"]])
        values = np.maximum(package.get("prediction_floor", 0.0), values)

        predictions = [
            {"station_id": station_id, "target_at": target["target_at"], "value": round(float(value), 3)}
            for station_id, target, value in zip(station_ids, cycle["targets"], values, strict=True)
        ]
        client_run_id = f"extra-trees-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
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
        if response.status_code >= 400:
            raise RuntimeError(f"Submission rejected ({response.status_code}): {response.text}")
        response.raise_for_status()
        result = response.json()
        print(json.dumps({"cycle_id": cycle["cycle_id"], "submission": result, "prediction_count": len(predictions)}, indent=2))


if __name__ == "__main__":
    main()
