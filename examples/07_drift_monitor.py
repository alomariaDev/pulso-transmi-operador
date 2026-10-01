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
MODEL_FAMILY_ID = "extra_trees_regressor_v1"
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
    observations = pd.read_csv(
        DATA_DIR / "observations.csv",
        dtype={"station_id": "string"},
        parse_dates=["observed_at"],
    )
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
    minimum_station_rows = int(0.9 * 7 * 96)
    for station_id in sorted(observations["station_id"].dropna().unique()):
        feature_name = f"demand_station_{station_id}"
        station_reference = reference_observations.loc[
            reference_observations["station_id"] == station_id, "demand"
        ]
        station_recent = recent_observations.loc[
            recent_observations["station_id"] == station_id, "demand"
        ]
        if min(station_reference.notna().sum(), station_recent.notna().sum()) < minimum_station_rows:
            metrics[feature_name] = None
            unavailable_features[feature_name] = (
                "demand coverage is below 90% in one of the 7-day windows "
                f"(recent={station_recent.notna().sum()}, "
                f"reference={station_reference.notna().sum()})"
            )
        else:
            metrics[feature_name] = psi(station_reference, station_recent)

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
    age_hours = max(
        0.0,
        (pd.Timestamp.now(tz="UTC") - pd.Timestamp(cutoff)).total_seconds() / 3600,
    )
    return {
        "reference_start": reference_start.isoformat(),
        "reference_end": recent_start.isoformat(),
        "recent_start": recent_start.isoformat(),
        "recent_end": cutoff.isoformat(),
        "threshold": DRIFT_THRESHOLD,
        "data_freshness": {
            "latest_observation_at": cutoff.isoformat(),
            "age_hours": age_hours,
            "stale": age_hours > 36,
            "stale_after_hours": 36,
        },
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


def _empty_counts() -> dict[str, int | float]:
    return {
        "prediction_count": 0,
        "resolved_count": 0,
        "absolute_error_sum": 0.0,
        "actual_sum": 0.0,
    }


def _merge_counts(target: dict[str, int | float], source: dict[str, int | float]) -> None:
    for key in target:
        target[key] += source[key]


def _metric_snapshot(counts: dict[str, int | float]) -> dict[str, float | int | None]:
    return accuracy_for_counts(
        int(counts["prediction_count"]),
        int(counts["resolved_count"]),
        float(counts["absolute_error_sum"]),
        float(counts["actual_sum"]),
    )


def evaluate_accuracy(database_url: str) -> dict[str, object]:
    with psycopg.connect(database_url, prepare_threshold=None) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select r.run_id, r.model_id, r.finished_at,
                       coalesce(nullif(r.metrics->>'expected_predictions', '')::integer, 0),
                       p.station_id, p.horizon,
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
                where (r.model_id = %s or r.model_id like %s)
                  and r.status = 'succeeded'
                group by r.run_id, r.model_id, r.finished_at,
                         r.metrics->>'expected_predictions', p.station_id, p.horizon
                order by r.finished_at desc nulls last, r.run_id, p.horizon
                """,
                (MODEL_FAMILY_ID, f"{MODEL_FAMILY_ID}:%"),
            )
            rows = cursor.fetchall()

            runs: dict[str, dict[str, object]] = {}
            for (run_id, model_id, finished_at, expected_count, station_id, horizon,
                 count, resolved, error_sum, actual_sum) in rows:
                if horizon not in HORIZONS:
                    continue
                run = runs.setdefault(
                    str(run_id),
                    {
                        "run_id": str(run_id),
                        "model_id": str(model_id),
                        "finished_at": finished_at,
                        "expected_predictions": int(expected_count or 0),
                        "counts": _empty_counts(),
                        "by_horizon": {h: _empty_counts() for h in HORIZONS},
                        "by_station_horizon": {},
                    },
                )
                cell = {
                    "prediction_count": int(count),
                    "resolved_count": int(resolved),
                    "absolute_error_sum": float(error_sum),
                    "actual_sum": float(actual_sum),
                }
                _merge_counts(run["counts"], cell)
                _merge_counts(run["by_horizon"][horizon], cell)
                station_cells = run["by_station_horizon"].setdefault(
                    str(station_id), {h: _empty_counts() for h in HORIZONS}
                )
                _merge_counts(station_cells[horizon], cell)

            run_order = sorted(
                runs,
                key=lambda key: (runs[key]["finished_at"] is not None,
                                 runs[key]["finished_at"], key),
                reverse=True,
            )
            recent_run_ids = set(run_order[:6])
            cumulative_horizons = {h: _empty_counts() for h in HORIZONS}
            recent_horizons = {h: _empty_counts() for h in HORIZONS}
            cumulative_station_horizon: dict[str, dict[int, dict[str, int | float]]] = {}
            recent_station_horizon: dict[str, dict[int, dict[str, int | float]]] = {}
            cumulative_total = _empty_counts()
            recent_total = _empty_counts()
            cumulative_expected = cumulative_recorded = 0
            recent_expected = recent_recorded = 0
            check_rows = []

            for run_id, run in runs.items():
                _merge_counts(cumulative_total, run["counts"])
                cumulative_expected += int(run["expected_predictions"])
                cumulative_recorded += int(run["counts"]["prediction_count"])
                for horizon in HORIZONS:
                    _merge_counts(cumulative_horizons[horizon], run["by_horizon"][horizon])
                for station_id, horizon_map in run["by_station_horizon"].items():
                    cumulative_station_horizon.setdefault(
                        station_id, {h: _empty_counts() for h in HORIZONS}
                    )
                    for horizon in HORIZONS:
                        _merge_counts(
                            cumulative_station_horizon[station_id][horizon],
                            horizon_map[horizon],
                        )
                if run_id in recent_run_ids:
                    _merge_counts(recent_total, run["counts"])
                    recent_expected += int(run["expected_predictions"])
                    recent_recorded += int(run["counts"]["prediction_count"])
                    for horizon in HORIZONS:
                        _merge_counts(recent_horizons[horizon], run["by_horizon"][horizon])
                    for station_id, horizon_map in run["by_station_horizon"].items():
                        recent_station_horizon.setdefault(
                            station_id, {h: _empty_counts() for h in HORIZONS}
                        )
                        for horizon in HORIZONS:
                            _merge_counts(
                                recent_station_horizon[station_id][horizon],
                                horizon_map[horizon],
                            )

            def with_status(counts: dict[str, int | float]) -> dict[str, object]:
                metric = _metric_snapshot(counts)
                metric["status"] = (
                    "passed"
                    if metric["prediction_count"] > 0
                    and metric["resolved_count"] == metric["prediction_count"]
                    and metric["accuracy"] is not None
                    else "warning"
                )
                return metric

            def summarize_horizons(source: dict[int, dict[str, int | float]]) -> dict[str, object]:
                return {str(h): with_status(source[h]) for h in HORIZONS}

            cumulative_by_horizon = summarize_horizons(cumulative_horizons)
            recent_by_horizon = summarize_horizons(recent_horizons)
            cumulative_overall = with_status(cumulative_total)
            recent_overall = with_status(recent_total)
            cumulative_overall["submission_coverage_pct"] = (
                100 * cumulative_recorded / cumulative_expected if cumulative_expected else None
            )
            recent_overall["submission_coverage_pct"] = (
                100 * recent_recorded / recent_expected if recent_expected else None
            )
            for metric, recorded, expected in (
                (cumulative_overall, cumulative_recorded, cumulative_expected),
                (recent_overall, recent_recorded, recent_expected),
            ):
                if not expected or recorded < expected:
                    metric["status"] = "warning"

            station_report = {}
            for station_id in sorted(set(cumulative_station_horizon) | set(recent_station_horizon)):
                station_report[station_id] = {
                    "cumulative": {
                        str(h): with_status(cumulative_station_horizon.get(
                            station_id, {h: _empty_counts() for h in HORIZONS}
                        )[h]) for h in HORIZONS
                    },
                    "recent_six_cycles": {
                        str(h): with_status(recent_station_horizon.get(
                            station_id, {h: _empty_counts() for h in HORIZONS}
                        )[h]) for h in HORIZONS
                    },
                }

            cycle_report = []
            for run_id in run_order[:6]:
                run = runs[run_id]
                cycle_report.append({
                    "run_id": run_id,
                    "model_id": run["model_id"],
                    "finished_at": run["finished_at"].isoformat() if run["finished_at"] else None,
                    "expected_predictions": run["expected_predictions"],
                    "persisted_predictions": run["counts"]["prediction_count"],
                    "accuracy": with_status(run["counts"]),
                    "by_horizon": summarize_horizons(run["by_horizon"]),
                })

            for window, report in (("cumulative", cumulative_by_horizon),
                                   ("recent_six_cycles", recent_by_horizon)):
                for horizon, metric in report.items():
                    check_rows.append((
                        f"competition_accuracy_{window}_{horizon}m",
                        metric["status"], metric["accuracy"],
                        json.dumps({"window": window, "horizon_minutes": int(horizon), **metric}),
                    ))
            for window, metric, expected, recorded in (
                ("cumulative", cumulative_overall, cumulative_expected, cumulative_recorded),
                ("recent_six_cycles", recent_overall, recent_expected, recent_recorded),
            ):
                check_rows.append((
                    f"competition_accuracy_{window}_overall", metric["status"],
                    metric["accuracy"], json.dumps({
                        "window": window, "horizons_minutes": HORIZONS,
                        "expected_predictions": expected, "persisted_predictions": recorded,
                        **metric,
                    }),
                ))
            for station_id, windows in station_report.items():
                for horizon, metric in windows["recent_six_cycles"].items():
                    check_rows.append((
                        f"accuracy_6c_{station_id}_{horizon}m", metric["status"],
                        metric["accuracy"], json.dumps({
                            "station_id": station_id, "horizon_minutes": int(horizon),
                            "window": "recent_six_cycles", **metric,
                        }),
                    ))

            for run_id, run in runs.items():
                metric = _metric_snapshot(run["counts"])
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
                            "horizons": summarize_horizons(run["by_horizon"]),
                            "model_id": run["model_id"],
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
        "model_family": MODEL_FAMILY_ID,
        "formula": "accuracy = 100 * max(0, 1 - WAPE)",
        "horizons_minutes": list(HORIZONS),
        "cycle_window": "six_most_recent_persisted_submission_cycles",
        "cumulative": {"by_horizon": cumulative_by_horizon, "overall": cumulative_overall},
        "recent_six_cycles": {"by_horizon": recent_by_horizon, "overall": recent_overall},
        "by_station_horizon": station_report,
        "cycles": cycle_report,
        "submission_runs": len(runs),
        "recent_cycle_count": min(6, len(runs)),
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
            recent_accuracy = accuracy_report["recent_six_cycles"]["overall"].get("accuracy")
            cumulative_accuracy = accuracy_report["cumulative"]["overall"].get("accuracy")
            output.write(f"accuracy={'n/a' if recent_accuracy is None else recent_accuracy}\n")
            output.write(
                f"cumulative_accuracy={'n/a' if cumulative_accuracy is None else cumulative_accuracy}\n"
            )


if __name__ == "__main__":
    main()
