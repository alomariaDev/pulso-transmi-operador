from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import httpx
import pandas as pd
import psycopg

from pulso_transmi import PulsoTransmiClient


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = Path(__file__).resolve().parent
API_URL = os.getenv("PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io").rstrip("/")
PARTICIPANT_NAME = os.getenv("DASHBOARD_PARTICIPANT_NAME", "Maria Isabell Guzman Faneyte")
HORIZONS = (15, 30, 45, 60)


def iso(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[no-any-return]
    return str(value)


def get_leaderboard(api_key: str, window: str) -> dict[str, object]:
    response = httpx.get(
        f"{API_URL}/v1/leaderboard",
        params={"window": window},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def fetch_observations(api_key: str) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    with tempfile.TemporaryDirectory(prefix="pulso-dashboard-") as temporary:
        temp_dir = Path(temporary)
        with PulsoTransmiClient(base_url=API_URL, api_key=api_key, timeout=120) as client:
            stations = client.stations()
            meta = client.meta()
            client.download("observations.csv", temp_dir / "observations.csv")
            stream = client.stream_observations_dataframe()
        base = pd.read_csv(
            temp_dir / "observations.csv",
            dtype={"station_id": "string"},
            parse_dates=["observed_at"],
        )
        observations = pd.concat([base, stream], ignore_index=True)
        observations["observed_at"] = pd.to_datetime(observations["observed_at"], utc=True)
        observations["station_id"] = observations["station_id"].astype("string")
        observations = (
            observations.drop_duplicates(["station_id", "observed_at"], keep="last")
            .sort_values(["observed_at", "station_id"])
            .reset_index(drop=True)
        )
    stations["station_id"] = stations["station_id"].astype("string")
    return stations, observations, meta


def _metric(actual: list[float], predicted: list[float], prediction_count: int) -> dict[str, object]:
    resolved = len(actual)
    actual_sum = sum(actual)
    error_sum = sum(abs(y - yhat) for y, yhat in zip(actual, predicted, strict=True))
    wape = error_sum / actual_sum if resolved and actual_sum else None
    return {
        "prediction_count": prediction_count,
        "resolved_count": resolved,
        "coverage_pct": 100 * resolved / prediction_count if prediction_count else 0.0,
        "wape": wape,
        "accuracy": max(0.0, 1 - wape) * 100 if wape is not None else None,
    }


def fetch_supabase(database_url: str) -> dict[str, object]:
    result: dict[str, object] = {
        "status": "unavailable",
        "active_model": None,
        "latest_run": None,
        "predictions": [],
        "errors": [],
        "error_by_horizon": [],
        "error_by_station": [],
        "drift": [],
    }
    with psycopg.connect(database_url, prepare_threshold=None) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select r.run_id, r.model_id, m.algorithm, m.feature_version,
                       m.code_commit, r.finished_at, r.train_rows, c.cutoff_at,
                       r.metrics
                from pulso.training_runs r
                join pulso.model_versions m using (model_id)
                left join pulso.data_cutoffs c using (cutoff_id)
                where r.status = 'succeeded'
                order by coalesce(r.finished_at, r.started_at) desc
                limit 1
                """
            )
            row = cursor.fetchone()
            if row:
                run_id, model_id, algorithm, feature_version, commit, finished, rows, cutoff, metrics = row
                result["active_model"] = {
                    "run_id": run_id,
                    "model_id": model_id,
                    "algorithm": algorithm,
                    "feature_version": feature_version,
                    "code_commit": commit,
                    "trained_at": iso(finished),
                    "training_rows": rows,
                    "data_cutoff": iso(cutoff),
                    "artifact_sha256": metrics.get("artifact_sha256") if metrics else None,
                    "cycle_id": metrics.get("cycle_id") if metrics else None,
                    "submission": metrics.get("submission") if metrics else None,
                }
                result["latest_run"] = {
                    "run_id": run_id,
                    "status": "success",
                    "finished_at": iso(finished),
                    "cycle_id": metrics.get("cycle_id") if metrics else None,
                    "submission_state": metrics.get("submission_state") if metrics else None,
                }

            cursor.execute(
                """
                select p.station_id, p.target_at, p.horizon, p.y_pred,
                       e.actual_demand, e.absolute_error, p.submitted_at
                from pulso.predictions p
                join pulso.prediction_evaluation e using (prediction_id)
                order by p.submitted_at desc, p.target_at desc
                limit 5000
                """
            )
            prediction_rows = cursor.fetchall()
            predictions = [
                {
                    "station_id": str(station_id),
                    "target_at": iso(target_at),
                    "horizon": int(horizon),
                    "predicted": float(predicted),
                    "actual": float(actual) if actual is not None else None,
                    "absolute_error": float(error) if error is not None else None,
                    "submitted_at": iso(submitted_at),
                }
                for station_id, target_at, horizon, predicted, actual, error, submitted_at in prediction_rows
            ]
            result["predictions"] = predictions

            resolved = [item for item in predictions if item["actual"] is not None]
            by_horizon: dict[int, list[dict[str, object]]] = defaultdict(list)
            by_station: dict[str, list[dict[str, object]]] = defaultdict(list)
            for item in predictions:
                by_horizon[int(item["horizon"])].append(item)
                by_station[str(item["station_id"])].append(item)

            def summarize(items: list[dict[str, object]]) -> dict[str, object]:
                actuals = [float(x["actual"]) for x in items if x["actual"] is not None]
                predicted = [float(x["predicted"]) for x in items if x["actual"] is not None]
                return _metric(actuals, predicted, len(items))

            result["error_by_horizon"] = [
                {"horizon": horizon, **summarize(by_horizon[horizon])}
                for horizon in HORIZONS
            ]
            result["error_by_station"] = [
                {"station_id": station_id, **summarize(items)}
                for station_id, items in sorted(by_station.items())
            ]
            result["errors"] = [
                float(item["absolute_error"])
                for item in resolved
                if item["absolute_error"] is not None
            ][-2000:]

            cursor.execute(
                """
                select distinct on (check_name)
                       check_name, status, value, details, observed_at
                from pulso.data_quality_checks
                where check_name like 'drift_psi_%'
                order by check_name, observed_at desc
                """
            )
            drift_rows = cursor.fetchall()
            result["drift"] = [
                {
                    "feature": check_name.removeprefix("drift_psi_"),
                    "status": status,
                    "psi": float(value) if value is not None else None,
                    "threshold": float(details.get("threshold", 0.2)),
                    "details": details,
                    "observed_at": iso(observed_at),
                }
                for check_name, status, value, details, observed_at in drift_rows
            ]
    result["status"] = "connected"
    return result


def fetch_last_action() -> dict[str, object] | None:
    repository = os.getenv("GITHUB_REPOSITORY", "alomariaDev/pulso-transmi-operador")
    token = os.getenv("GITHUB_TOKEN", "")
    url = f"https://api.github.com/repos/{repository}/actions/runs?branch=main&per_page=100"
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=20) as response:
            runs = json.load(response).get("workflow_runs", [])
        run = next(
            (item for item in runs if "pulso-transmi-pipeline.yml" in item.get("path", "")),
            None,
        )
        if not run:
            return None
        return {
            "name": run.get("name"),
            "status": run.get("conclusion") or run.get("status"),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "html_url": run.get("html_url"),
            "run_number": run.get("run_number"),
            "event": run.get("event"),
        }
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None


def create_summary() -> dict[str, object]:
    api_key = os.getenv("PULSO_API_KEY")
    if not api_key:
        raise RuntimeError("PULSO_API_KEY es necesaria para generar el dashboard")
    database_url = os.getenv("SUPABASE_DB_URL", "").strip()
    stations, observations, meta = fetch_observations(api_key)

    now = datetime.now(timezone.utc)
    latest_observation = observations["observed_at"].max()
    age_hours = max(0.0, (pd.Timestamp(now) - latest_observation).total_seconds() / 3600)
    cutoff = latest_observation - pd.Timedelta(days=7)
    recent = observations.loc[observations["observed_at"] >= cutoff]
    station_rows = []
    for station in stations.to_dict("records"):
        station_id = str(station["station_id"])
        series = observations.loc[observations["station_id"] == station_id]
        station_recent = recent.loc[recent["station_id"] == station_id]
        hourly = station_recent.assign(hour=station_recent["observed_at"].dt.hour).groupby("hour")["demand"].mean()
        hourly_curve = [round(float(hourly[hour]), 1) if hour in hourly.index else None for hour in range(24)]
        values = series["demand"].astype(float)
        station_rows.append({
            **station,
            "station_id": station_id,
            "mean_demand": round(float(values.mean()), 1) if len(values) else 0,
            "max_demand": round(float(values.max()), 1) if len(values) else 0,
            "latest_demand": float(series.iloc[-1]["demand"]) if len(series) else None,
            "latest_at": iso(series.iloc[-1]["observed_at"]) if len(series) else None,
            "record_count": int(len(series)),
            "hourly_curve": hourly_curve,
        })

    daily = observations.assign(date=observations["observed_at"].dt.strftime("%Y-%m-%d"))
    daily_trend = [
        {"date": str(date), "avg_demand": round(float(group["demand"].mean()), 1)}
        for date, group in daily.groupby("date", sort=True)
    ][-60:]

    leaderboard: dict[str, object] = {"cumulative": None, "rolling_24h": None, "top": []}
    for window in ("cumulative", "rolling_24h"):
        data = get_leaderboard(api_key, window)
        entries = data.get("data", [])
        own = next((entry for entry in entries if entry.get("display_name") == PARTICIPANT_NAME), None)
        if own:
            leaderboard[window] = {
                "accuracy": own.get("accuracy"),
                "wape": own.get("raw_wape"),
                "coverage": own.get("coverage"),
                "rank": own.get("rank"),
                "eligible": own.get("eligible"),
                "calculated_at": own.get("calculated_at"),
                "resolved_cycles": data.get("resolved_cycles"),
            }
        if window == "cumulative":
            leaderboard["top"] = [
                {
                    "rank": entry.get("rank"),
                    "name": entry.get("display_name"),
                    "accuracy": entry.get("accuracy"),
                    "coverage": entry.get("coverage"),
                    "is_self": entry.get("display_name") == PARTICIPANT_NAME,
                }
                for entry in sorted(entries, key=lambda item: item.get("rank") or 999)[:15]
            ]

    supabase = fetch_supabase(database_url) if database_url else {"status": "not_configured"}
    all_predictions = supabase.get("predictions", []) if isinstance(supabase, dict) else []
    by_station_predictions: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in all_predictions:
        by_station_predictions[str(item["station_id"])].append(item)
    time_series = {}
    for station in station_rows:
        station_id = station["station_id"]
        obs_series = observations.loc[observations["station_id"] == station_id].tail(96)
        predicted_series = sorted(
            by_station_predictions.get(station_id, []), key=lambda item: item["target_at"]
        )[-48:]
        time_series[station_id] = {
            "actual": [
                {"timestamp": iso(row.observed_at), "value": float(row.demand)}
                for row in obs_series.itertuples()
            ],
            "predicted": [
                {
                    "timestamp": item["target_at"],
                    "value": item["predicted"],
                    "actual": item["actual"],
                    "horizon": item["horizon"],
                }
                for item in predicted_series
            ],
        }

    return {
        "metadata": {
            "title": "Pulso TransMi — MLOps Dashboard",
            "generated_at": now.isoformat(),
            "total_stations": len(station_rows),
            "total_observations": int(len(observations)),
            "latest_observation_at": iso(latest_observation),
            "data_age_hours": round(age_hours, 1),
            "data_stale": age_hours > 36,
            "data_stale_after_hours": 36,
            "date_range_start": iso(observations["observed_at"].min()),
            "date_range_end": iso(latest_observation),
            "api_version": meta.get("api_version"),
            "supabase_status": supabase.get("status", "not_configured") if isinstance(supabase, dict) else "unavailable",
            "participant_name": PARTICIPANT_NAME,
        },
        "leaderboard": leaderboard,
        "stations": station_rows,
        "daily_trend": daily_trend,
        "time_series": time_series,
        "mlops": {
            "active_model": supabase.get("active_model") if isinstance(supabase, dict) else None,
            "latest_run": supabase.get("latest_run") if isinstance(supabase, dict) else None,
            "last_pipeline_action": fetch_last_action(),
            "supabase_status": supabase.get("status", "not_configured") if isinstance(supabase, dict) else "unavailable",
        },
        "errors": {
            "by_horizon": supabase.get("error_by_horizon", []) if isinstance(supabase, dict) else [],
            "by_station": supabase.get("error_by_station", []) if isinstance(supabase, dict) else [],
            "absolute_error_sample": supabase.get("errors", []) if isinstance(supabase, dict) else [],
        },
        "drift": supabase.get("drift", []) if isinstance(supabase, dict) else [],
    }


def main() -> None:
    for env_path in (ROOT / ".env", ROOT.parent / "pulso-transmi-sdk" / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key and key not in os.environ:
                os.environ[key] = value.strip().strip('"').strip("'")
    payload = create_summary()
    output_path = DASHBOARD_DIR / "data" / "summary_data.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    data_js_path = DASHBOARD_DIR / "data.js"
    data_js_path.write_text(
        "window.PULSO_DASHBOARD_DATA = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
    )

    print(
        f"Dashboard snapshot: {output_path} "
        f"({output_path.stat().st_size:,} bytes, "
        f"{payload['metadata']['total_stations']} estaciones, "
        f"{payload['metadata']['total_observations']:,} observaciones)"
    )
    print(f"Static bootstrap: {data_js_path} ({data_js_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
