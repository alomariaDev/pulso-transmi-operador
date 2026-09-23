import pandas as pd
import importlib.util
from pathlib import Path

module_path = Path(__file__).parents[1] / "examples" / "07_drift_monitor.py"
spec = importlib.util.spec_from_file_location("drift_monitor", module_path)
drift_monitor = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(drift_monitor)


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
