from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from pulso_transmi import PulsoTransmiClient


DATA_DIR = Path("data")
REPORT_PATH = Path("artifacts/drift_report.json")
DRIFT_THRESHOLD = 0.20


def psi(reference: pd.Series, recent: pd.Series, bins: int = 10) -> float:
    combined = pd.concat([reference, recent]).dropna().astype(float)
    if combined.empty:
        return 0.0
    edges = np.unique(np.quantile(combined, np.linspace(0, 1, bins + 1)))
    if len(edges) < 2:
        return 0.0
    reference_counts, _ = np.histogram(reference.dropna(), bins=edges)
    recent_counts, _ = np.histogram(recent.dropna(), bins=edges)
    reference_share = np.clip(reference_counts / max(reference_counts.sum(), 1), 1e-6, None)
    recent_share = np.clip(recent_counts / max(recent_counts.sum(), 1), 1e-6, None)
    return float(np.sum((recent_share - reference_share) * np.log(recent_share / reference_share)))


def load_data(api_url: str, api_key: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with PulsoTransmiClient(base_url=api_url, api_key=api_key, timeout=120.0) as client:
        for filename in ("observations.csv", "context.csv"):
            client.download(filename, DATA_DIR / filename)
    observations = pd.read_csv(DATA_DIR / "observations.csv", parse_dates=["observed_at"])
    context = pd.read_csv(DATA_DIR / "context.csv", parse_dates=["observed_at"])
    return observations, context


def build_report(observations: pd.DataFrame, context: pd.DataFrame) -> dict[str, object]:
    cutoff = observations["observed_at"].max()
    recent_start = cutoff - pd.Timedelta(days=7)
    reference_start = recent_start - pd.Timedelta(days=7)
    recent_observations = observations.loc[observations["observed_at"] > recent_start]
    reference_observations = observations.loc[
        (observations["observed_at"] > reference_start)
        & (observations["observed_at"] <= recent_start)
    ]
    recent_context = context.loc[context["observed_at"] > recent_start]
    reference_context = context.loc[
        (context["observed_at"] > reference_start)
        & (context["observed_at"] <= recent_start)
    ]
    metrics: dict[str, float] = {
        "demand": psi(reference_observations["demand"], recent_observations["demand"]),
        "rain_mm": psi(reference_context["rain_mm"], recent_context["rain_mm"]),
        "temperature_c": psi(reference_context["temperature_c"], recent_context["temperature_c"]),
        "event_intensity": psi(reference_context["event_intensity"], recent_context["event_intensity"]),
    }
    return {
        "reference_start": reference_start.isoformat(),
        "reference_end": recent_start.isoformat(),
        "recent_start": recent_start.isoformat(),
        "recent_end": cutoff.isoformat(),
        "threshold": DRIFT_THRESHOLD,
        "metrics": metrics,
        "drifted_features": [name for name, value in metrics.items() if value >= DRIFT_THRESHOLD],
        "drift_detected": any(value >= DRIFT_THRESHOLD for value in metrics.values()),
    }


def main() -> None:
    api_url = os.getenv("PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io")
    api_key = os.getenv("PULSO_API_KEY")
    if not api_key:
        raise RuntimeError("PULSO_API_KEY no está configurada")
    observations, context = load_data(api_url, api_key)
    report = build_report(observations, context)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    output_path = os.getenv("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"drift_detected={'true' if report['drift_detected'] else 'false'}\n")


if __name__ == "__main__":
    main()
