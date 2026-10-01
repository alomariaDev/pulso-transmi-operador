from __future__ import annotations

import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor

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
DIRECT_HORIZONS_MINUTES = (45, 60)


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

    direct_models: dict[int, HistGradientBoostingRegressor] = {}
    direct_model_training_rows: dict[str, int] = {}
    for horizon in DIRECT_HORIZONS_MINUTES:
        steps = horizon // 15
        target = featured.groupby("station_id", sort=False)["demand"].shift(-steps)
        eligible = (
            target.notna()
            & (featured["observed_at"] + pd.Timedelta(minutes=horizon) <= training_data_end)
        )
        direct_training = featured.loc[eligible]
        direct_age_days = (
            training_data_end - direct_training["observed_at"]
        ).dt.total_seconds() / 86_400
        direct_sample_weight = np.exp(
            -np.log(2) * direct_age_days / RECENCY_HALF_LIFE_DAYS
        )
        direct_model = HistGradientBoostingRegressor(
            max_iter=250,
            learning_rate=0.08,
            max_leaf_nodes=31,
            l2_regularization=1.0,
            random_state=42,
        )
        direct_model.fit(
            direct_training[FEATURE_COLUMNS],
            target.loc[eligible],
            sample_weight=direct_sample_weight,
        )
        direct_models[horizon] = direct_model
        direct_model_training_rows[str(horizon)] = len(direct_training)

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    package = {
        "model": model,
        "direct_models": direct_models,
        "model_name": "extra_trees_hybrid_direct_45_60",
        "model_version": "extra_trees_hybrid_direct_h45_h60_v1",
        "algorithm": "ExtraTreesRegressor+HistGradientBoostingRegressor",
        "feature_version": "lag_features_direct_h45_h60_v1",
        "feature_columns": FEATURE_COLUMNS,
        "station_codes": station_codes,
        "target": "demand",
        "training_rows": len(featured),
        "direct_model_training_rows": direct_model_training_rows,
        "data_start": observations["observed_at"].min().isoformat(),
        "data_end": training_data_end.isoformat(),
        "parameters": {
            **model.get_params(),
            "sample_weight_strategy": "exponential_recency_decay",
            "sample_weight_half_life_days": RECENCY_HALF_LIFE_DAYS,
            "direct_model_algorithm": "HistGradientBoostingRegressor",
            "direct_horizons_minutes": list(DIRECT_HORIZONS_MINUTES),
            "direct_max_iter": 250,
            "direct_learning_rate": 0.08,
            "direct_max_leaf_nodes": 31,
            "direct_l2_regularization": 1.0,
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
    print("Direct horizons: 45 and 60 minutes")
    print(f"Tamaño: {MODEL_PATH.stat().st_size:,} bytes")
    if mlflow_run_id:
        print(f"MLflow run: {mlflow_run_id}")


if __name__ == "__main__":
    main()
