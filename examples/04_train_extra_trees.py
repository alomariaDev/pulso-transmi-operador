from __future__ import annotations

import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor

from model_features import build_features
from mlflow_tracking import has_drift_run_for_cutoff, log_training_run


DATA_DIR = Path("data")
ARTIFACT_DIR = Path("artifacts")
MODEL_PATH = ARTIFACT_DIR / "extra_trees_demand.joblib"
FEATURE_COLUMNS = [
    "station_code",
    "lag_15m",
    "lag_1h",
    "lag_1d",
    "lag_7d",
    "rolling_mean_1h",
    "rolling_mean_1d",
    "rolling_std_1d",
    "hour",
    "quarter_hour",
    "weekday",
    "is_weekend",
]
RECENCY_HALF_LIFE_DAYS = 14.0


def main() -> None:
    observations = pd.read_csv(
        DATA_DIR / "observations.csv",
        dtype={"station_id": "string"},
        parse_dates=["observed_at"],
    )
    training_data_end = pd.to_datetime(observations["observed_at"], utc=True).max()
    trigger = os.getenv("TRAINING_TRIGGER", "cycle_or_manual")
    if trigger == "drift" and has_drift_run_for_cutoff(training_data_end.isoformat()):
        print(
            "Se omite reentrenamiento de drift: MLflow ya tiene un modelo para "
            f"el corte {training_data_end.isoformat()}."
        )
        return
    context = pd.read_csv(DATA_DIR / "context.csv", parse_dates=["observed_at"])
    featured = build_features(observations, context)
    featured = featured.dropna(subset=[*FEATURE_COLUMNS, "demand"]).reset_index(drop=True)
    station_values = sorted(observations["station_id"].dropna().unique().tolist())
    station_codes = {station_id: code for code, station_id in enumerate(station_values)}
    featured["station_code"] = featured["station_id"].map(station_codes)

    model = ExtraTreesRegressor(
        n_estimators=250,
        min_samples_leaf=2,
        max_features=0.9,
        n_jobs=-1,
        random_state=42,
    )
    age_days = (
        training_data_end - featured["observed_at"]
    ).dt.total_seconds() / 86_400
    sample_weight = np.exp(-np.log(2) * age_days / RECENCY_HALF_LIFE_DAYS)
    model.fit(
        featured[FEATURE_COLUMNS],
        featured["demand"],
        sample_weight=sample_weight,
    )

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    package = {
        "model": model,
        "model_name": "extra_trees_regressor",
        "feature_columns": FEATURE_COLUMNS,
        "station_codes": station_codes,
        "target": "demand",
        "training_rows": len(featured),
        "data_start": observations["observed_at"].min().isoformat(),
        "data_end": training_data_end.isoformat(),
        "parameters": {
            **model.get_params(),
            "sample_weight_strategy": "exponential_recency_decay",
            "sample_weight_half_life_days": RECENCY_HALF_LIFE_DAYS,
        },
        "prediction_floor": 0.0,
    }
    joblib.dump(package, MODEL_PATH, compress=3)
    try:
        mlflow_run_id = log_training_run(
            package=package,
            model_path=MODEL_PATH,
            trigger=trigger,
        )
    except Exception as error:
        if os.getenv("MLFLOW_REQUIRED", "false").lower() == "true":
            raise
        print(f"Advertencia: MLflow no disponible; se conserva el Joblib local ({type(error).__name__}).")
        mlflow_run_id = None
    print(f"Modelo guardado en: {MODEL_PATH}")
    print(f"Filas de entrenamiento: {len(featured):,}")
    print(f"Features: {len(FEATURE_COLUMNS)}")
    print(f"Tamaño: {MODEL_PATH.stat().st_size:,} bytes")
    if mlflow_run_id:
        print(f"MLflow run: {mlflow_run_id}")


if __name__ == "__main__":
    main()
