from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.metrics import mean_absolute_error


DATA_DIR = Path("data")
ARTIFACT_DIR = Path("artifacts")


def wape(actual: pd.Series, prediction: np.ndarray) -> float:
    denominator = actual.sum()
    if denominator == 0:
        return 0.0
    return float(np.abs(actual.to_numpy() - prediction).sum() / denominator)


def build_features(observations: pd.DataFrame, context: pd.DataFrame) -> pd.DataFrame:
    frame = observations.sort_values(["station_id", "observed_at"]).copy()
    frame = frame.merge(context, on="observed_at", how="left", validate="many_to_one")
    grouped_demand = frame.groupby("station_id")["demand"]
    for periods, name in ((1, "lag_15m"), (4, "lag_1h"), (96, "lag_1d"), (672, "lag_7d")):
        frame[name] = grouped_demand.shift(periods)
    shifted = grouped_demand.shift(1)
    frame["rolling_mean_1h"] = shifted.groupby(frame["station_id"]).transform(lambda values: values.rolling(4, min_periods=4).mean())
    frame["rolling_mean_1d"] = shifted.groupby(frame["station_id"]).transform(lambda values: values.rolling(96, min_periods=96).mean())
    frame["rolling_std_1d"] = shifted.groupby(frame["station_id"]).transform(lambda values: values.rolling(96, min_periods=96).std())
    frame["hour"] = frame["observed_at"].dt.hour
    frame["quarter_hour"] = frame["observed_at"].dt.minute // 15
    frame["weekday"] = frame["observed_at"].dt.dayofweek
    frame["is_weekend"] = (frame["weekday"] >= 5).astype(int)
    frame["station_code"] = frame["station_id"].astype("category").cat.codes
    return frame


def accuracy(wape_value: float) -> float:
    return max(0.0, 100.0 * (1.0 - wape_value))


def main() -> None:
    observations = pd.read_csv(DATA_DIR / "observations.csv", dtype={"station_id": "string"}, parse_dates=["observed_at"])
    context = pd.read_csv(DATA_DIR / "context.csv", parse_dates=["observed_at"])
    frame = build_features(observations, context).dropna().reset_index(drop=True)
    cutoff = frame["observed_at"].max() - timedelta(days=7)
    train = frame.loc[frame["observed_at"] <= cutoff].copy()
    validation = frame.loc[frame["observed_at"] > cutoff].copy()
    feature_columns = [
        "station_code", "lag_15m", "lag_1h", "lag_1d", "lag_7d",
        "rolling_mean_1h", "rolling_mean_1d", "rolling_std_1d",
        "hour", "quarter_hour", "weekday", "is_weekend",
        "rain_mm", "rain_forecast", "temperature_c", "temperature_forecast", "event_intensity",
    ]
    x_train, y_train = train[feature_columns], train["demand"]
    x_validation, y_validation = validation[feature_columns], validation["demand"]
    models = {
        "seasonal_naive_1d": None,
        "hist_gradient_boosting": HistGradientBoostingRegressor(max_iter=250, learning_rate=0.08, max_leaf_nodes=31, l2_regularization=1.0, random_state=42),
        "random_forest": RandomForestRegressor(n_estimators=250, min_samples_leaf=2, max_features=0.8, n_jobs=-1, random_state=42),
        "extra_trees": ExtraTreesRegressor(n_estimators=250, min_samples_leaf=2, max_features=0.9, n_jobs=-1, random_state=42),
    }
    results: list[dict[str, object]] = []
    naive_prediction = validation["lag_1d"].to_numpy()
    naive_wape = wape(y_validation, naive_prediction)
    results.append({"model": "seasonal_naive_1d", "wape": naive_wape, "accuracy": accuracy(naive_wape)})
    for model_name, model in list(models.items())[1:]:
        model.fit(x_train, y_train)
        prediction = np.maximum(0.0, model.predict(x_validation))
        model_wape = wape(y_validation, prediction)
        results.append({"model": model_name, "wape": model_wape, "accuracy": accuracy(model_wape), "mae": mean_absolute_error(y_validation, prediction)})
    ranking = pd.DataFrame(results).sort_values("wape").reset_index(drop=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    ranking.to_csv(ARTIFACT_DIR / "model_comparison.csv", index=False)
    print(f"Corte temporal: {cutoff.isoformat()}")
    print(f"Entrenamiento: {len(train):,} filas | Validacion: {len(validation):,} filas")
    print(ranking.to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print(f"Mejor modelo por WAPE: {ranking.iloc[0]['model']}")


if __name__ == "__main__":
    main()
