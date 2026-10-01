from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import joblib
import numpy as np
import pandas as pd
import psycopg

from model_features import build_features


BASE_URL = os.getenv("PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io").rstrip("/")
DATA_DIR = Path("data")
MODEL_PATH = Path("artifacts/extra_trees_demand.joblib")
MODEL_FAMILY_ID = "extra_trees_regressor_v1"
FEATURE_VERSION = "lag_features_v1"
CALIBRATION_STATION = "05100"
CALIBRATION_CYCLES = 12
CALIBRATION_MIN_CYCLES = 8


def load_env_file() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def predict_targets(
    observations: pd.DataFrame,
    context: pd.DataFrame,
    targets: list[dict[str, object]],
    package: dict[str, object],
) -> list[dict[str, object]]:
    working_observations = observations.copy()
    working_context = context.copy()
    values_by_key: dict[tuple[str, str], float] = {}

    target_times = sorted({target["target_at"] for target in targets})
    for target_at_text in target_times:
        target_at = pd.Timestamp(target_at_text)
        targets_at = [target for target in targets if target["target_at"] == target_at_text]
        station_ids = [str(target["station_id"]) for target in targets_at]
        target_rows = pd.DataFrame(
            {"observed_at": [target_at] * len(station_ids), "station_id": station_ids, "demand": [np.nan] * len(station_ids)}
        )
        featured = build_features(
            pd.concat([working_observations, target_rows], ignore_index=True),
            working_context,
        )
        prediction_rows = featured.loc[
            (featured["observed_at"] == target_at) & featured["station_id"].isin(station_ids)
        ].copy()
        prediction_rows["station_code"] = prediction_rows["station_id"].map(package["station_codes"])
        prediction_rows = prediction_rows.set_index("station_id").loc[station_ids]
        values = np.maximum(
            package.get("prediction_floor", 0.0),
            package["model"].predict(prediction_rows[package["feature_columns"]]),
        )
        for station_id, value in zip(station_ids, values, strict=True):
            values_by_key[(station_id, target_at_text)] = round(float(value), 3)
        generated = target_rows.copy()
        generated["demand"] = values
        working_observations = pd.concat([working_observations, generated], ignore_index=True)

    predictions = [
        {
            "station_id": str(target["station_id"]),
            "target_at": target["target_at"],
            "value": values_by_key[(str(target["station_id"]), str(target["target_at"]))],
        }
        for target in targets
    ]
    expected_keys = [(str(target["station_id"]), str(target["target_at"])) for target in targets]
    if len(predictions) != len(targets) or len(set(expected_keys)) != len(expected_keys):
        raise RuntimeError("Los targets del ciclo contienen claves duplicadas o incompletas")
    return predictions


def calibrate_station_predictions(
    predictions: list[dict[str, object]],
    targets: list[dict[str, object]],
) -> dict[str, object]:
    """Correct the historically overpredicted station using only resolved past cycles."""
    database_url = os.getenv("SUPABASE_DB_URL", "").strip()
    result: dict[str, object] = {
        "method": "none",
        "station_id": CALIBRATION_STATION,
        "reason": "SUPABASE_DB_URL is unavailable",
        "applied_predictions": 0,
    }
    if not database_url:
        print("Sin historial de Supabase; se conservan las predicciones originales.")
        return result

    try:
        with psycopg.connect(database_url, connect_timeout=10, prepare_threshold=None) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    with resolved_runs as (
                        select p.run_id, max(p.submitted_at) as submitted_at
                        from pulso.predictions p
                        join pulso.training_runs r using (run_id)
                        join pulso.model_versions m using (model_id)
                        join pulso.prediction_evaluation e using (prediction_id)
                        where m.algorithm = 'ExtraTreesRegressor'
                          and m.feature_version = %s
                        group by p.run_id
                        having count(*) = 48
                           and count(e.actual_demand) = 48
                        order by max(p.submitted_at) desc
                        limit %s
                    )
                    select p.horizon,
                           count(distinct p.run_id) as resolved_cycles,
                           count(*) as prediction_count,
                           sum(e.actual_demand) as actual_sum,
                           sum(p.y_pred) as predicted_sum
                    from resolved_runs rr
                    join pulso.predictions p using (run_id)
                    join pulso.prediction_evaluation e using (prediction_id)
                    where p.station_id = %s
                      and p.horizon in (15, 30, 45, 60)
                    group by p.horizon
                    """,
                    (FEATURE_VERSION, CALIBRATION_CYCLES, CALIBRATION_STATION),
                )
                rows = cursor.fetchall()
    except psycopg.Error as error:
        result["reason"] = f"Supabase history unavailable ({type(error).__name__})"
        print("No se pudo leer el historial; se conservan las predicciones originales.")
        return result

    factors: dict[int, float] = {}
    evidence: dict[str, dict[str, int | float]] = {}
    for horizon, resolved_cycles, prediction_count, actual_sum, predicted_sum in rows:
        if resolved_cycles < CALIBRATION_MIN_CYCLES or not predicted_sum:
            continue
        factor = float(np.clip(float(actual_sum) / float(predicted_sum), 0.75, 1.25))
        factors[int(horizon)] = factor
        evidence[str(int(horizon))] = {
            "resolved_cycles": int(resolved_cycles),
            "prediction_count": int(prediction_count),
            "factor": factor,
        }

    target_horizons = {
        (str(target["station_id"]), str(target["target_at"])): int(target["horizon_minutes"])
        for target in targets
    }
    applied = 0
    if factors:
        for prediction in predictions:
            if str(prediction["station_id"]) != CALIBRATION_STATION:
                continue
            key = (str(prediction["station_id"]), str(prediction["target_at"]))
            factor = factors.get(target_horizons.get(key, -1))
            if factor is None:
                continue
            prediction["value"] = round(max(0.0, float(prediction["value"]) * factor), 3)
            applied += 1

    result = {
        "method": "historical_station_horizon_ratio",
        "station_id": CALIBRATION_STATION,
        "reference_cycles": CALIBRATION_CYCLES,
        "minimum_cycles": CALIBRATION_MIN_CYCLES,
        "clip_range": [0.75, 1.25],
        "horizon_factors": evidence,
        "applied_predictions": applied,
        "reason": None if applied else "insufficient resolved history for requested horizons",
    }
    if applied:
        print(
            f"Calibradas {applied} predicciones de la estación {CALIBRATION_STATION} "
            "con los últimos ciclos resueltos."
        )
    else:
        print("Historial insuficiente para calibrar; se conservan las predicciones originales.")
    return result


def persist_submission(
    cycle: dict[str, object],
    package: dict[str, object],
    predictions: list[dict[str, object]],
    submission: dict[str, object],
    submission_state: str,
) -> None:
    database_url = os.getenv("SUPABASE_DB_URL", "").strip()
    if not database_url:
        print("SUPABASE_DB_URL no está configurada; no se persistieron las predicciones.")
        return

    cycle_id = str(cycle["cycle_id"])
    run_id = f"extra-trees-{cycle_id}"
    cutoff_id = cycle_id
    artifact_sha256 = hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()
    model_id = f"{MODEL_FAMILY_ID}:{artifact_sha256[:16]}"
    cutoff_at = pd.Timestamp(str(cycle["data_cutoff"])).to_pydatetime()
    train_start = pd.Timestamp(str(package["data_start"])).to_pydatetime()
    training_data_end = pd.Timestamp(str(package["data_end"])).to_pydatetime()
    trained_at = datetime.fromtimestamp(MODEL_PATH.stat().st_mtime, timezone.utc)
    finished_at = datetime.now(timezone.utc)
    code_commit = os.getenv("GITHUB_SHA", "local")
    run_metrics = {
        "cycle_id": cycle_id,
        "submission_state": submission_state,
        "submission": submission,
        "expected_predictions": len(cycle["targets"]),
        "persisted_predictions": len(predictions),
        "data_cutoff": str(cycle["data_cutoff"]),
        "artifact_sha256": artifact_sha256,
        "model_family": MODEL_FAMILY_ID,
        "prediction_calibration": package.get("prediction_calibration", {"method": "none"}),
    }
    hyperparameters = json.dumps(package.get("parameters", {}), default=str)
    metrics = json.dumps(run_metrics, ensure_ascii=False, default=str)

    prediction_by_key = {
        (str(row["station_id"]), str(row["target_at"])): float(row["value"])
        for row in predictions
    }
    prediction_rows = [
        (
            run_id,
            str(target["station_id"]),
            pd.Timestamp(str(target["target_at"])).to_pydatetime(),
            int(target["horizon_minutes"]),
            prediction_by_key[(str(target["station_id"]), str(target["target_at"]))],
        )
        for target in cycle["targets"]
    ]

    with psycopg.connect(database_url, prepare_threshold=None) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select model_id from pulso.training_runs where run_id = %s",
                (run_id,),
            )
            existing_run = cursor.fetchone()
            if existing_run:
                print(
                    f"El ciclo {cycle_id} ya está registrado en Supabase "
                    f"(model_id={existing_run[0]}); no se cambia su versión."
                )
                return

            cursor.execute(
                """
                insert into pulso.model_versions
                    (model_id, algorithm, feature_version, code_commit, hyperparameters)
                values (%s, %s, %s, %s, %s::jsonb)
                on conflict (model_id) do nothing
                """,
                (model_id, "ExtraTreesRegressor", FEATURE_VERSION, code_commit, hyperparameters),
            )
            cursor.execute(
                """
                insert into pulso.data_cutoffs
                    (cutoff_id, cutoff_at, train_start, validation_start,
                     validation_end, split_strategy)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (cutoff_id) do nothing
                """,
                (
                    cutoff_id,
                    cutoff_at,
                    train_start,
                    training_data_end,
                    training_data_end,
                    "competition_full_history_until_cutoff",
                ),
            )
            cursor.execute(
                """
                insert into pulso.training_runs
                    (run_id, model_id, cutoff_id, status, started_at, finished_at,
                     train_rows, validation_rows, metrics)
                values (%s, %s, %s, 'succeeded', %s, %s, %s, 0, %s::jsonb)
                on conflict (run_id) do nothing
                """,
                (
                    run_id,
                    model_id,
                    cutoff_id,
                    trained_at,
                    finished_at,
                    int(package.get("training_rows", 0)),
                    metrics,
                ),
            )
            cursor.executemany(
                """
                insert into pulso.predictions
                    (run_id, station_id, target_at, horizon, y_pred)
                values (%s, %s, %s, %s, %s)
                on conflict (run_id, station_id, target_at, horizon) do nothing
                """,
                prediction_rows,
            )
        connection.commit()
    print(
        f"Predicciones guardadas en Supabase: run_id={run_id}, "
        f"count={len(prediction_rows)}, state={submission_state}"
    )


def main(expected_cycle_id: str | None = None) -> None:
    load_env_file()
    api_key = os.environ.get("PULSO_API_KEY")
    if not api_key:
        raise RuntimeError("PULSO_API_KEY no está configurada")

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=60.0) as client:
        cycle_response = client.get("/v1/forecast-cycles/current")
        if cycle_response.status_code == 404:
            detail = cycle_response.json().get("detail", {})
            if detail == "no_open_cycle" or (
                isinstance(detail, dict) and detail.get("code") == "no_open_cycle"
            ):
                print(
                    "El ciclo se cerró antes del envío; no se envió ninguna predicción. "
                    "La siguiente ejecución consultará el ciclo vigente."
                )
                return
        cycle_response.raise_for_status()
        cycle = cycle_response.json()

        if expected_cycle_id is not None and cycle["cycle_id"] != expected_cycle_id:
            print(
                "El ciclo cambió durante el entrenamiento; se omite el envío para "
                "evitar asociar predicciones al ciclo equivocado. "
                f"Esperado={expected_cycle_id}; actual={cycle['cycle_id']}."
            )
            return

        if cycle["state"] != "open":
            print(
                f"El ciclo ya no está abierto (state={cycle['state']}); "
                "no se envió ninguna predicción."
            )
            return

        package = joblib.load(MODEL_PATH)
        observations = pd.read_csv(
            DATA_DIR / "observations.csv",
            dtype={"station_id": "string"},
            parse_dates=["observed_at"],
        )
        context = pd.read_csv(DATA_DIR / "context.csv", parse_dates=["observed_at"])
        targets = cycle["targets"]
        if len(targets) != cycle["expected_predictions"]:
            raise RuntimeError("La API publicó una cantidad de targets inconsistente")
        predictions = predict_targets(observations, context, targets, package)
        package["prediction_calibration"] = calibrate_station_predictions(predictions, targets)
        client_run_id = f"extra-trees-{cycle['cycle_id']}"
        payload = {
            "schema_version": "1.0",
            "cycle_id": cycle["cycle_id"],
            "client_run_id": client_run_id,
            "data_cutoff": cycle["data_cutoff"],
            "model": {
                "version": "extra_trees_regressor_v1",
                "trained_at": datetime.fromtimestamp(MODEL_PATH.stat().st_mtime, timezone.utc).isoformat(),
                "training_data_end": package["data_end"],
                "git_commit": None,
            },
            "predictions": predictions,
        }
        response = client.post(
            "/v1/submissions",
            json=payload,
            headers={"Idempotency-Key": client_run_id},
        )
        if response.status_code == 409:
            detail = response.json().get("detail", {})
            if isinstance(detail, dict) and detail.get("code") == "idempotency_conflict":
                persist_submission(
                    cycle,
                    package,
                    predictions,
                    {"status": "already_submitted"},
                    "already_submitted",
                )
                print(
                    json.dumps(
                        {
                            "cycle_id": cycle["cycle_id"],
                            "status": "already_submitted",
                            "message": "La API ya recibió una submission con esta clave de idempotencia; se conserva la aceptada.",
                        },
                        ensure_ascii=False,
                    )
                )
                return
        if response.status_code >= 400:
            raise RuntimeError(f"Submission rejected ({response.status_code}): {response.text}")
        response.raise_for_status()
        result = response.json()
        persist_submission(cycle, package, predictions, result, "accepted")
        print(json.dumps({"cycle_id": cycle["cycle_id"], "submission": result, "prediction_count": len(predictions)}, indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 2:
        raise SystemExit("Uso: python examples/05_submit_predictions.py [cycle_id]")
    main(sys.argv[1] if len(sys.argv) == 2 else None)
