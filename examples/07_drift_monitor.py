from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg

from pulso_transmi import PulsoTransmiClient


DATA_DIR = Path("data")
REPORT_PATH = Path("artifacts/drift_report.json")
DRIFT_THRESHOLD = 0.20
MODEL_ID = "extra_trees_regressor_v1"
HORIZONS = (15, 30, 45, 60)
ACCURACY_REPORT_PATH = Path("artifacts/accuracy_report.json")


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
        stream = client.stream_observations_dataframe()
    observations = pd.read_csv(DATA_DIR / "observations.csv", parse_dates=["observed_at"])
    observations = pd.concat([observations, stream], ignore_index=True)
    observations["observed_at"] = pd.to_datetime(observations["observed_at"], utc=True)
    observations["station_id"] = observations["station_id"].astype("string")
    observations = observations.drop_duplicates(
        ["station_id", "observed_at"], keep="last"
    )
    observations = observations.sort_values(
        ["observed_at", "station_id"]
    ).reset_index(drop=True)
    observations.to_csv(DATA_DIR / "observations.csv", index=False)
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
    metrics: dict[str, float | None] = {
        "demand": psi(reference_observations["demand"], recent_observations["demand"]),
    }
    unavailable_features: dict[str, str] = {}
    minimum_context_rows = int(0.9 * 7 * 96)
    for name in ("rain_mm", "temperature_c", "event_intensity"):
        recent_count = recent_context.loc[
            recent_context[name].notna(), "observed_at"
        ].nunique()
        reference_count = reference_context.loc[
            reference_context[name].notna(), "observed_at"
        ].nunique()
        if min(recent_count, reference_count) < minimum_context_rows:
            metrics[name] = None
            unavailable_features[name] = (
                "context coverage is below 90% in one of the 7-day windows "
                f"(recent={recent_count}, reference={reference_count})"
            )
        else:
            metrics[name] = psi(reference_context[name], recent_context[name])
    available_metrics = [value for value in metrics.values() if value is not None]
    return {
        "reference_start": reference_start.isoformat(),
        "reference_end": recent_start.isoformat(),
        "recent_start": recent_start.isoformat(),
        "recent_end": cutoff.isoformat(),
        "threshold": DRIFT_THRESHOLD,
        "metrics": metrics,
        "unavailable_features": unavailable_features,
        "drifted_features": [
            name for name, value in metrics.items()
            if value is not None and value >= DRIFT_THRESHOLD
        ],
        "drift_detected": any(value >= DRIFT_THRESHOLD for value in available_metrics),
    }


def accuracy_for_counts(
    prediction_count: int,
    resolved_count: int,
    absolute_error_sum: float,
    actual_sum: float,
) -> dict[str, float | int | None]:
    wape = absolute_error_sum / actual_sum if resolved_count and actual_sum > 0 else None
    accuracy = max(0.0, 1.0 - wape) * 100 if wape is not None else None
    return {
        "prediction_count": prediction_count,
        "resolved_count": resolved_count,
        "coverage_pct": (100.0 * resolved_count / prediction_count) if prediction_count else 0.0,
        "wape": wape,
        "accuracy": accuracy,
    }


def evaluate_accuracy(database_url: str) -> dict[str, object]:
    with psycopg.connect(database_url, prepare_threshold=None) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select r.run_id, r.finished_at, p.horizon,
                       count(p.prediction_id) as prediction_count,
                       count(e.actual_demand) as resolved_count,
                       coalesce(sum(e.absolute_error)
                           filter (where e.actual_demand is not null), 0) as absolute_error_sum,
                       coalesce(sum(e.actual_demand)
                           filter (where e.actual_demand is not null), 0) as actual_sum
                from pulso.training_runs r
                join pulso.predictions p on p.run_id = r.run_id
                left join pulso.prediction_evaluation e
                    on e.prediction_id = p.prediction_id
                where r.model_id = %s and r.status = 'succeeded'
                group by r.run_id, r.finished_at, p.horizon
                order by r.finished_at desc nulls last, r.run_id, p.horizon
                """,
                (MODEL_ID,),
            )
            rows = cursor.fetchall()

            by_horizon = {
                horizon: {"prediction_count": 0, "resolved_count": 0,
                          "absolute_error_sum": 0.0, "actual_sum": 0.0}
                for horizon in HORIZONS
            }
            by_run: dict[str, dict[str, object]] = {}
            for run_id, finished_at, horizon, count, resolved, error_sum, actual_sum in rows:
                if horizon not in by_horizon:
                    continue
                horizon_totals = by_horizon[horizon]
                horizon_totals["prediction_count"] += int(count)
                horizon_totals["resolved_count"] += int(resolved)
                horizon_totals["absolute_error_sum"] += float(error_sum)
                horizon_totals["actual_sum"] += float(actual_sum)

                run = by_run.setdefault(
                    str(run_id),
                    {"finished_at": finished_at, "prediction_count": 0,
                     "resolved_count": 0, "absolute_error_sum": 0.0,
                     "actual_sum": 0.0, "horizons": {}},
                )
                run["prediction_count"] += int(count)
                run["resolved_count"] += int(resolved)
                run["absolute_error_sum"] += float(error_sum)
                run["actual_sum"] += float(actual_sum)
                run["horizons"][str(horizon)] = accuracy_for_counts(
                    int(count), int(resolved), float(error_sum), float(actual_sum)
                )

            horizon_report: dict[str, object] = {}
            check_rows = []
            for horizon, totals in by_horizon.items():
                metric = accuracy_for_counts(
                    int(totals["prediction_count"]),
                    int(totals["resolved_count"]),
                    float(totals["absolute_error_sum"]),
                    float(totals["actual_sum"]),
                )
                metric["status"] = (
                    "passed"
                    if metric["prediction_count"] > 0
                    and metric["resolved_count"] == metric["prediction_count"]
                    else "warning"
                )
                horizon_report[str(horizon)] = metric
                check_rows.append(
                    (
                        f"competition_accuracy_{horizon}m",
                        metric["status"],
                        metric["accuracy"],
                        json.dumps({"horizon_minutes": horizon, **metric}),
                    )
                )

            total_predictions = sum(int(v["prediction_count"]) for v in by_horizon.values())
            total_resolved = sum(int(v["resolved_count"]) for v in by_horizon.values())
            total_error = sum(float(v["absolute_error_sum"]) for v in by_horizon.values())
            total_actual = sum(float(v["actual_sum"]) for v in by_horizon.values())
            overall = accuracy_for_counts(
                total_predictions, total_resolved, total_error, total_actual
            )
            overall["status"] = (
                "passed"
                if total_predictions > 0 and total_resolved == total_predictions
                else "warning"
            )
            check_rows.append(
                (
                    "competition_accuracy_overall",
                    overall["status"],
                    overall["accuracy"],
                    json.dumps({"horizons_minutes": HORIZONS, **overall}),
                )
            )

            for run_id, run in by_run.items():
                metric = accuracy_for_counts(
                    int(run["prediction_count"]),
                    int(run["resolved_count"]),
                    float(run["absolute_error_sum"]),
                    float(run["actual_sum"]),
                )
                cursor.execute(
                    """
                    update pulso.training_runs
                    set validation_rows = %s,
                        wape = %s,
                        accuracy = %s,
                        metrics = metrics || %s::jsonb
                    where run_id = %s
                    """,
                    (
                        metric["resolved_count"],
                        metric["wape"],
                        metric["accuracy"],
                        json.dumps({"competition_evaluation": {
                            **metric,
                            "horizons": run["horizons"],
                            "evaluated_at": datetime.now(timezone.utc).isoformat(),
                        }}),
                        run_id,
                    ),
                )

            cursor.executemany(
                """
                insert into pulso.data_quality_checks
                    (check_name, status, value, details)
                values (%s, %s, %s, %s::jsonb)
                """,
                check_rows,
            )
        connection.commit()

    return {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "model_id": MODEL_ID,
        "formula": "accuracy = 100 * max(0, 1 - WAPE)",
        "horizons_minutes": list(HORIZONS),
        "by_horizon": horizon_report,
        "overall": overall,
        "submission_runs": len(by_run),
    }


def persist_drift_checks(database_url: str, report: dict[str, object]) -> None:
    rows = []
    metrics = report["metrics"]
    unavailable = report["unavailable_features"]
    for feature, value in metrics.items():
        status = "warning" if value is None or value >= DRIFT_THRESHOLD else "passed"
        rows.append(
            (
                f"drift_psi_{feature}",
                status,
                value,
                json.dumps({
                    "feature": feature,
                    "psi": value,
                    "threshold": DRIFT_THRESHOLD,
                    "drift_detected": feature in report["drifted_features"],
                    "reason_unavailable": unavailable.get(feature),
                    "reference_start": report["reference_start"],
                    "reference_end": report["reference_end"],
                    "recent_start": report["recent_start"],
                    "recent_end": report["recent_end"],
                }),
            )
        )
    with psycopg.connect(database_url, prepare_threshold=None) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                insert into pulso.data_quality_checks
                    (check_name, status, value, details)
                values (%s, %s, %s, %s::jsonb)
                """,
                rows,
            )
        connection.commit()


def main() -> None:
    api_url = os.getenv("PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io")
    api_key = os.getenv("PULSO_API_KEY")
    database_url = os.getenv("SUPABASE_DB_URL", "").strip()
    if not api_key:
        raise RuntimeError("PULSO_API_KEY no está configurada")
    if not database_url:
        raise RuntimeError("SUPABASE_DB_URL no está configurada")
    observations, context = load_data(api_url, api_key)
    report = build_report(observations, context)
    persist_drift_checks(database_url, report)
    accuracy_report = evaluate_accuracy(database_url)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    ACCURACY_REPORT_PATH.write_text(json.dumps(accuracy_report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(json.dumps({"accuracy_report": accuracy_report}, indent=2))
    output_path = os.getenv("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"drift_detected={'true' if report['drift_detected'] else 'false'}\n")
            accuracy = accuracy_report["overall"].get("accuracy")
            output.write(f"accuracy={'n/a' if accuracy is None else accuracy}\n")


if __name__ == "__main__":
    main()
