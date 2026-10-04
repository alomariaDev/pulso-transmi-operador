from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

from model_features import build_features
from model_config import HORIZON_MODEL_CONFIG


DATA_DIR = Path("data")
ARTIFACT_DIR = Path("artifacts")
FEATURE_COLUMNS = [
    "station_code", "lag_15m", "lag_1h", "lag_1d", "lag_7d",
    "rolling_mean_1h", "rolling_mean_1d", "rolling_std_1d",
    "same_hour_prev_day", "same_hour_prev_week",
    "day_over_day_change", "week_over_week_change", "station_level_shift",
    "hour", "quarter_hour", "weekday", "is_weekend",
]


def wape(actual: pd.Series, prediction: np.ndarray) -> float:
    denominator = actual.sum()
    if denominator == 0:
        return 0.0
    return float(np.abs(actual.to_numpy() - prediction).sum() / denominator)


def accuracy(wape_value: float) -> float:
    return max(0.0, 100.0 * (1.0 - wape_value))


def evaluate_horizon(frame: pd.DataFrame, horizon_minutes: int) -> pd.DataFrame:
    steps = horizon_minutes // 15
    horizon_frame = frame.copy()
    horizon_frame[f"target_{horizon_minutes}m"] = horizon_frame.groupby("station_id", sort=False)["demand"].shift(-steps)
    horizon_frame["seasonal_baseline"] = horizon_frame.groupby("station_id", sort=False)["demand"].shift(96 - steps)
    horizon_frame = horizon_frame.dropna(subset=[*FEATURE_COLUMNS, f"target_{horizon_minutes}m"]).copy()
    rows: list[dict[str, object]] = []
    latest = horizon_frame["observed_at"].max()
    for window_index in (3, 2, 1):
        window_end = latest - timedelta(days=(window_index - 1) * 7)
        window_start = window_end - timedelta(days=7)
        train = horizon_frame.loc[horizon_frame["observed_at"] <= window_start].copy()
        validation = horizon_frame.loc[
            (horizon_frame["observed_at"] > window_start)
            & (horizon_frame["observed_at"] <= window_end)
        ].copy()
        if train.empty or validation.empty:
            continue
        x_train, y_train = train[FEATURE_COLUMNS], train[f"target_{horizon_minutes}m"]
        x_validation, y_validation = validation[FEATURE_COLUMNS], validation[f"target_{horizon_minutes}m"]
        age_days = (train["observed_at"].max() - train["observed_at"]).dt.total_seconds() / 86_400
        hgb_config = HORIZON_MODEL_CONFIG[horizon_minutes]
        model_configs = {
            "hgb_relative_target": (
                hgb_config["loss"], hgb_config["half_life_days"], "relative"
            ),
            "hgb_absolute_target": (
                hgb_config["loss"], hgb_config["half_life_days"], "absolute"
            ),
        }
        fitted_models = {
            name: HistGradientBoostingRegressor(
                loss=loss,
                max_iter=250,
                learning_rate=0.08,
                max_leaf_nodes=31,
                l2_regularization=1.0,
                random_state=42,
            )
            for name, (loss, _, _) in model_configs.items()
        }
        for model_name, model in fitted_models.items():
            model_config = model_configs[model_name]
            half_life_days = model_config[1]
            target_mode = model_config[2]
            model_sample_weight = np.exp(-np.log(2) * age_days / half_life_days)
            fit_target = y_train
            if target_mode == "relative":
                fit_target = y_train / train["lag_15m"].clip(lower=10.0)
            model.fit(x_train, fit_target, sample_weight=model_sample_weight)
            prediction = model.predict(x_validation)
            if target_mode == "relative":
                prediction = prediction * validation["lag_15m"].clip(lower=10.0).to_numpy()
            prediction = np.maximum(0.0, prediction)
            model_wape = wape(y_validation, prediction)
            rows.append(
                {
                    "horizon_minutes": horizon_minutes,
                    "window_start": window_start.date().isoformat(),
                    "window_end": window_end.date().isoformat(),
                    "model": model_name,
                    "half_life_days": half_life_days,
                    "wape": model_wape,
                    "accuracy": accuracy(model_wape),
                    "mae": mean_absolute_error(y_validation, prediction),
                    "absolute_error_sum": float(np.abs(y_validation.to_numpy() - prediction).sum()),
                    "actual_sum": float(y_validation.sum()),
                    "train_rows": len(train),
                    "validation_rows": len(validation),
                }
            )

        baseline_prediction = np.maximum(0.0, validation["seasonal_baseline"].to_numpy())
        baseline_wape = wape(y_validation, baseline_prediction)
        rows.append(
            {
                "horizon_minutes": horizon_minutes,
                "window_start": window_start.date().isoformat(),
                "window_end": window_end.date().isoformat(),
                "model": "seasonal_naive_24h",
                "wape": baseline_wape,
                "accuracy": accuracy(baseline_wape),
                "mae": mean_absolute_error(y_validation, baseline_prediction),
                "absolute_error_sum": float(np.abs(y_validation.to_numpy() - baseline_prediction).sum()),
                "actual_sum": float(y_validation.sum()),
                "train_rows": len(train),
                "validation_rows": len(validation),
            }
        )
    return pd.DataFrame(rows).sort_values("wape").reset_index(drop=True)


def main() -> None:
    observations = pd.read_csv(DATA_DIR / "observations.csv", dtype={"station_id": "string"}, parse_dates=["observed_at"])
    context = pd.read_csv(DATA_DIR / "context.csv", parse_dates=["observed_at"])
    frame = build_features(observations, context)
    frame = frame.dropna(subset=[*FEATURE_COLUMNS, "demand"]).reset_index(drop=True)

    results: list[dict[str, object]] = []
    for horizon in (15, 30, 45, 60):
        horizon_results = evaluate_horizon(frame, horizon)
        results.extend(horizon_results.to_dict("records"))

    ranking = pd.DataFrame(results).sort_values(["horizon_minutes", "wape"]).reset_index(drop=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    ranking.to_csv(ARTIFACT_DIR / "model_comparison.csv", index=False)

    print("Comparación de targets por horizonte (tres ventanas temporales de 7 días)")
    print(ranking.to_string(index=False, float_format=lambda value: f"{value:.4f}"))

    for horizon in (15, 30, 45, 60):
        summary = (
            ranking.loc[ranking["horizon_minutes"] == horizon]
            .groupby("model", as_index=False)
            .agg(
                absolute_error_sum=("absolute_error_sum", "sum"),
                actual_sum=("actual_sum", "sum"),
                windows=("wape", "count"),
            )
        )
        summary["aggregate_wape"] = summary["absolute_error_sum"] / summary["actual_sum"]
        summary["aggregate_accuracy"] = 100.0 * (1.0 - summary["aggregate_wape"])
        summary = summary.sort_values("aggregate_wape")
        best = summary.iloc[0]
        print(
            f"Mejor promedio para {horizon} min: {best['model']} "
            f"({best['aggregate_accuracy']:.2f}% aggregate accuracy, "
            f"{int(best['windows'])} ventanas)"
        )


if __name__ == "__main__":
    main()
