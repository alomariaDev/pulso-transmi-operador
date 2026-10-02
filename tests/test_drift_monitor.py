import pandas as pd
import importlib.util
from pathlib import Path

module_path = Path(__file__).parents[1] / "examples" / "07_drift_monitor.py"
spec = importlib.util.spec_from_file_location("drift_monitor", module_path)
drift_monitor = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(drift_monitor)

features_path = Path(__file__).parents[1] / "examples" / "model_features.py"
features_spec = importlib.util.spec_from_file_location("model_features", features_path)
model_features = importlib.util.module_from_spec(features_spec)
assert features_spec.loader is not None
features_spec.loader.exec_module(model_features)

submission_path = Path(__file__).parents[1] / "examples" / "05_submit_predictions.py"
submission_spec = importlib.util.spec_from_file_location("submit_predictions", submission_path)
submit_predictions = importlib.util.module_from_spec(submission_spec)
assert submission_spec.loader is not None
submission_spec.loader.exec_module(submit_predictions)

training_path = Path(__file__).parents[1] / "examples" / "04_train_extra_trees.py"
training_spec = importlib.util.spec_from_file_location("train_extra_trees", training_path)
train_extra_trees = importlib.util.module_from_spec(training_spec)
assert training_spec.loader is not None
training_spec.loader.exec_module(train_extra_trees)


def test_psi_is_zero_for_identical_distributions() -> None:
    values = pd.Series([1, 2, 3, 4, 5])
    assert drift_monitor.psi(values, values) == 0.0


def test_report_contains_drifted_features() -> None:
    timestamps = pd.date_range("2026-01-01", periods=14 * 96, freq="15min", tz="UTC")
    observations = pd.DataFrame(
        {
            "observed_at": timestamps,
            "station_id": "02300",
            "demand": [100] * (len(timestamps) // 2) + [10000] * (len(timestamps) // 2),
        }
    )
    context = pd.DataFrame(
        {
            "observed_at": timestamps,
            "rain_mm": 0.0,
            "temperature_c": 14.0,
            "event_intensity": 0.0,
        }
    )
    report = drift_monitor.build_report(observations, context)
    assert report["drift_detected"] is True
    assert "demand" in report["drifted_features"]


def test_build_features_includes_anti_drift_signals() -> None:
    timestamps = pd.date_range("2026-01-01 00:00:00", periods=384, freq="15min", tz="UTC")
    base_demands = [100, 110, 120, 130] * 96
    observations = pd.DataFrame(
        {
            "observed_at": timestamps,
            "station_id": ["A"] * len(timestamps),
            "demand": base_demands,
        }
    )
    context = pd.DataFrame(
        {
            "observed_at": timestamps,
            "rain_mm": 0.0,
            "temperature_c": 20.0,
            "event_intensity": 0.0,
        }
    )

    frame = model_features.build_features(observations, context)

    for column in [
        "lag_15m",
        "lag_1h",
        "lag_1d",
        "lag_7d",
        "rolling_mean_1h",
        "rolling_mean_1d",
        "rolling_std_1d",
        "hour",
        "weekday",
        "is_weekend",
        "same_hour_prev_day",
        "day_over_day_change",
        "station_level_shift",
    ]:
        assert column in frame.columns, f"Falta feature anti-drift: {column}"

    mature_rows = frame.iloc[96:]
    assert mature_rows["same_hour_prev_day"].notna().all()
    assert mature_rows["day_over_day_change"].notna().all()
    assert mature_rows["station_level_shift"].notna().all()


def test_target_demand_does_not_change_its_features() -> None:
    timestamps = pd.date_range("2026-01-01 00:00:00", periods=800, freq="15min", tz="UTC")
    observations = pd.DataFrame(
        {
            "observed_at": timestamps,
            "station_id": ["A"] * len(timestamps),
            "demand": [100 + index % 24 for index in range(len(timestamps))],
        }
    )
    context = pd.DataFrame({"observed_at": timestamps})
    original = model_features.build_features(observations, context)
    changed = observations.copy()
    changed.loc[500, "demand"] = 100_000
    modified = model_features.build_features(changed, context)

    feature_columns = [
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
    ]
    pd.testing.assert_series_equal(
        original.loc[500, feature_columns],
        modified.loc[500, feature_columns],
    )


def test_direct_horizon_model_is_used_for_submission() -> None:
    timestamps = pd.date_range("2026-01-01 00:00:00", periods=800, freq="15min", tz="UTC")
    observations = pd.DataFrame(
        {
            "observed_at": timestamps,
            "station_id": ["A"] * len(timestamps),
            "demand": [100 + index % 24 for index in range(len(timestamps))],
        }
    )
    context = pd.DataFrame({"observed_at": timestamps})

    class FixedPredictor:
        def predict(self, features: pd.DataFrame) -> list[float]:
            assert len(features) == 1
            return [321.0]

    predictions = submit_predictions.predict_targets(
        observations,
        context,
        [
            {
                "station_id": "A",
                "target_at": (timestamps[-1] + pd.Timedelta(minutes=15)).isoformat(),
                "horizon_minutes": 15,
            }
        ],
        {
            "direct_models": {15: FixedPredictor()},
            "station_codes": {"A": 0},
            "feature_columns": [
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
            ],
            "prediction_floor": 0.0,
        },
    )

    assert predictions[0]["value"] == 321.0


def test_station_drift_summary_marks_drifted_stations() -> None:
    timestamps = pd.date_range("2026-01-01 00:00:00", periods=384, freq="15min", tz="UTC")
    observations = pd.DataFrame(
        {
            "observed_at": timestamps,
            "station_id": ["A"] * len(timestamps),
            "demand": list(range(1, len(timestamps) + 1)),
        }
    )
    context = pd.DataFrame(
        {
            "observed_at": timestamps,
            "rain_mm": 0.0,
            "temperature_c": 20.0,
            "event_intensity": 0.0,
        }
    )
    features = model_features.build_features(observations, context)
    summary = train_extra_trees.summarize_station_drift(features, threshold=0.15)
    assert summary["drift_detected"].isin([True, False]).all()
    assert "station_id" in summary.columns
