from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


EXPERIMENT_NAME = "pulso-transmi-operador"


def _tracking_uri() -> str | None:
    configured = os.getenv("MLFLOW_TRACKING_URI", "").strip()
    if configured:
        return configured
    database_url = os.getenv("SUPABASE_DB_URL", "").strip()
    if not database_url:
        return None
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg2://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return database_url


def log_training_run(
    *, package: dict[str, Any], model_path: Path, trigger: str
) -> str | None:
    """Track model lineage in MLflow backed by Supabase Postgres."""
    tracking_uri = _tracking_uri()
    if not tracking_uri:
        print("MLflow omitido: configura SUPABASE_DB_URL o MLFLOW_TRACKING_URI.")
        return None

    import mlflow
    from mlflow.tracking import MlflowClient

    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient()
    experiment = client.get_experiment_by_name(EXPERIMENT_NAME)
    if experiment is None:
        artifact_root = Path("artifacts/mlflow").resolve()
        artifact_root.mkdir(parents=True, exist_ok=True)
        experiment_id = client.create_experiment(
            EXPERIMENT_NAME, artifact_location=artifact_root.as_uri()
        )
    else:
        experiment_id = experiment.experiment_id

    artifact_sha256 = hashlib.sha256(model_path.read_bytes()).hexdigest()
    data_cutoff = str(package["data_end"])
    drift_report_path = Path("artifacts/drift_report.json")
    drift_metrics: dict[str, float] = {}
    if drift_report_path.exists():
        report = json.loads(drift_report_path.read_text(encoding="utf-8"))
        for feature, value in report.get("metrics", {}).items():
            if value is not None:
                drift_metrics[f"psi_{feature}"] = float(value)
        if report.get("drift_detected"):
            trigger = "drift:" + ",".join(report.get("drifted_features", []))

    with mlflow.start_run(
        experiment_id=experiment_id,
        run_name=f"extra-trees-{trigger[:80]}-{data_cutoff[:16]}",
    ) as run:
        mlflow.set_tags(
            {
                "model_family": "extra_trees_regressor_v1",
                "training_trigger": trigger,
                "data_cutoff": data_cutoff,
                "data_start": str(package["data_start"]),
                "code_commit": os.getenv("GITHUB_SHA", "local"),
                "artifact_sha256": artifact_sha256,
                "feature_version": "lag_features_v1",
            }
        )
        params = package.get("parameters", {})
        mlflow.log_params(
            {
                str(key): str(value)[:500]
                for key, value in params.items()
                if value is not None and not isinstance(value, (dict, list, tuple))
            }
        )
        mlflow.log_metrics(
            {
                "training_rows": float(package["training_rows"]),
                "feature_count": float(len(package["feature_columns"])),
                **drift_metrics,
            }
        )
        mlflow.log_artifact(str(model_path), artifact_path="model")
        run_id = run.info.run_id
        output_path = os.getenv("GITHUB_OUTPUT")
        if output_path:
            with open(output_path, "a", encoding="utf-8") as output:
                output.write(f"mlflow_run_id={run_id}\n")
        return run_id


def has_drift_run_for_cutoff(data_cutoff: str) -> bool:
    """Avoid repeating hourly drift retraining when the API data has not changed."""
    tracking_uri = _tracking_uri()
    if not tracking_uri:
        if os.getenv("MLFLOW_REQUIRED", "false").lower() == "true":
            raise RuntimeError("MLflow tracking is required for drift retraining")
        return False

    import mlflow
    from mlflow.tracking import MlflowClient

    mlflow.set_tracking_uri(tracking_uri)
    experiment = MlflowClient().get_experiment_by_name(EXPERIMENT_NAME)
    if experiment is None:
        return False
    runs = MlflowClient().search_runs(
        experiment_ids=[experiment.experiment_id],
        filter_string=f"tags.data_cutoff = '{data_cutoff}'",
        max_results=100,
    )
    return any(
        run.info.status == "FINISHED"
        and run.data.tags.get("training_trigger", "").startswith("drift")
        for run in runs
    )
