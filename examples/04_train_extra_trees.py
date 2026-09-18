from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor

from model_features import build_features


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
    "rain_mm",
    "rain_forecast",
    "temperature_c",
    "temperature_forecast",
    "event_intensity",
]


def main() -> None:
    observations = pd.read_csv(
        DATA_DIR / "observations.csv",
        dtype={"station_id": "string"},
        parse_dates=["observed_at"],
    )
    context = pd.read_csv(DATA_DIR / "context.csv", parse_dates=["observed_at"])
    featured = build_features(observations, context).dropna().reset_index(drop=True)
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
    model.fit(featured[FEATURE_COLUMNS], featured["demand"])

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    package = {
        "model": model,
        "model_name": "extra_trees_regressor",
        "feature_columns": FEATURE_COLUMNS,
        "station_codes": station_codes,
        "target": "demand",
        "training_rows": len(featured),
        "data_start": observations["observed_at"].min().isoformat(),
        "data_end": observations["observed_at"].max().isoformat(),
        "parameters": model.get_params(),
        "prediction_floor": 0.0,
    }
    joblib.dump(package, MODEL_PATH, compress=3)
    print(f"Modelo guardado en: {MODEL_PATH}")
    print(f"Filas de entrenamiento: {len(featured):,}")
    print(f"Features: {len(FEATURE_COLUMNS)}")
    print(f"Tamaño: {MODEL_PATH.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
