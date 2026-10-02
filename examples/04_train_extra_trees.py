from __future__ import annotations

import os
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

EXAMPLES_DIR = Path(__file__).resolve().parent
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

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
    "same_hour_prev_day",
    "same_hour_prev_week",
    "day_over_day_change",
    "week_over_week_change",
    "station_level_shift",
    "hour",
    "quarter_hour",
    "weekday",
    "is_weekend",
]
RECENCY_HALF_LIFE_DAYS = 14.0
DIRECT_HORIZONS_MINUTES = (15, 30, 45, 60)


def summarize_station_drift(frame: pd.DataFrame, threshold: float = 0.15) -> pd.DataFrame:
    station_groups = frame.sort_values(["station_id", "observed_at"]).groupby("station_id", sort=False)
    recent_mean = station_groups["demand"].transform(
        lambda values: values.rolling(window=96, min_periods=96).mean().iloc[-1]
    )
    baseline_mean = station_groups["demand"].transform(
        lambda values: values.shift(96).rolling(window=96, min_periods=96).mean().iloc[-1]
    )
    summary = (
        pd.DataFrame(
            {
                "station_id": frame["station_id"],
                "recent_mean_demand": recent_mean,
                "baseline_mean_demand": baseline_mean,
            }
        )
        .drop_duplicates()
        .reset_index(drop=True)
    )
    summary["drift_ratio"] = (
        (summary["recent_mean_demand"] - summary["baseline_mean_demand"]).abs()
        / summary["baseline_mean_demand"].abs().clip(lower=1e-6)
    )
    summary["drift_detected"] = summary["drift_ratio"] >= threshold
    return summary.sort_values("drift_ratio", ascending=False).reset_index(drop=True)


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
    station_drift_summary = summarize_station_drift(featured, threshold=0.15)
    drifted_stations = station_drift_summary.loc[
        station_drift_summary["drift_detected"], "station_id"
    ].tolist()
    station_values = sorted(observations["station_id"].dropna().unique().tolist())
    station_codes = {station_id: code for code, station_id in enumerate(station_values)}
    featured["station_code"] = featured["station_id"].map(station_codes)
    if drifted_stations:
        print(
            "Estaciones con drift acumulado: "
            + ", ".join(str(station_id) for station_id in drifted_stations[:8])
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
        direct_recency_weight = np.exp(
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
            sample_weight=direct_recency_weight,
        )
        direct_models[horizon] = direct_model
        direct_model_training_rows[str(horizon)] = len(direct_training)

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    package = {
        "direct_models": direct_models,
        "model_name": "horizon_specific_direct_ensemble",
        "model_version": "hist_gradient_boosting_direct_h15_h30_h45_h60_v3",
        "algorithm": "HistGradientBoostingRegressor",
        "feature_version": "causal_lag_features_direct_all_horizons_v2",
        "feature_columns": FEATURE_COLUMNS,
        "station_codes": station_codes,
        "target": "demand",
        "training_rows": len(featured),
        "direct_model_training_rows": direct_model_training_rows,
        "data_start": observations["observed_at"].min().isoformat(),
        "data_end": training_data_end.isoformat(),
        "parameters": {
            "sample_weight_strategy": "exponential_recency_decay",
            "sample_weight_half_life_days": RECENCY_HALF_LIFE_DAYS,
            "station_drift_threshold": 0.15,
            "drifted_stations": drifted_stations[:20],
            "station_drift_summary": station_drift_summary.head(20).to_dict(orient="records"),
            "context_features_excluded": ["rain_mm", "temperature_c", "event_intensity"],
            "direct_model_algorithm": "HistGradientBoostingRegressor",
            "direct_horizons_minutes": list(DIRECT_HORIZONS_MINUTES),
            "direct_hist_gradient_boosting_max_iter": 250,
            "direct_hist_gradient_boosting_learning_rate": 0.08,
            "direct_hist_gradient_boosting_max_leaf_nodes": 31,
            "direct_hist_gradient_boosting_l2_regularization": 1.0,
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
    print("Direct horizons: 15, 30, 45 and 60 minutes")
    print(f"Tamaño: {MODEL_PATH.stat().st_size:,} bytes")
    if mlflow_run_id:
        print(f"MLflow run: {mlflow_run_id}")


if __name__ == "__main__":
    main()
