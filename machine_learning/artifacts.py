"""Versiones inmutables y puntero atómico. La API solo lee JSON, nunca pickle."""

from datetime import datetime, timezone
import hashlib
from importlib.metadata import version as package_version
import json
import os
from pathlib import Path
import platform
import re
import tempfile
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]


class ArtifactError(Exception):
    pass


def artifact_directory(env=None):
    env = os.environ if env is None else env
    path = Path(env.get("ML_ARTIFACT_DIR", "artifacts/ml-sales"))
    return path if path.is_absolute() else ROOT / path


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8")


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def validate_report(report):
    if report["schema_version"] != 1:
        raise ValueError("Versión de artefacto incompatible.")
    UUID(report["model_version"])
    UUID(report["source_publication"]["id"])
    datetime.fromisoformat(report["trained_at_utc"].replace("Z", "+00:00"))
    if len(report["forecast"]) != 3 or len(report["history"]) < 36:
        raise ValueError("Artefacto incompleto.")
    if report["selected_method"] not in {"ridge", "seasonal_naive"}:
        raise ValueError("Método incompatible.")
    for split in ("validation", "test"):
        for method in ("ridge", "seasonal_naive"):
            for metric in ("mae", "rmse"):
                value = report["metrics"][split][method][metric]
                if type(value) not in (int, float) or not 0 <= value < float("inf"):
                    raise ValueError("Métrica incompatible.")


class ReportStore:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else artifact_directory()

    def load(self):
        try:
            pointer_file = self.root / "current.json"
            if not pointer_file.exists():
                return None
            if pointer_file.stat().st_size > 4096:
                raise ValueError("Puntero inválido.")
            pointer = json.loads(pointer_file.read_text(encoding="utf-8"))
            identifier = str(UUID(pointer["model_version"]))
            expected = pointer["report_sha256"]
            if not re.fullmatch(r"[a-f0-9]{64}", expected):
                raise ValueError("Checksum inválido.")
            file = (self.root / "versions" / identifier / "report.json").resolve()
            if not file.is_relative_to(self.root.resolve()) or file.stat().st_size > 5_000_000:
                raise ValueError("Artefacto inválido.")
            payload = file.read_bytes()
            if sha256(payload) != expected:
                raise ValueError("Artefacto incompleto o modificado.")
            report = json.loads(payload)
            validate_report(report)
            if report["model_version"] != identifier:
                raise ValueError("Versión incompatible.")
            return report
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ArtifactError("No fue posible leer una versión válida del modelo.") from None

    def publish(self, model, evaluation, snapshot, dump=None, versions=None):
        if dump is None:
            from joblib import dump
        if versions is None:
            versions = {name: package_version(name) for name in ("scikit-learn", "numpy", "scipy", "joblib", "threadpoolctl")}
        identifier = str(uuid4())
        directory = self.root / "versions" / identifier
        directory.mkdir(parents=True, exist_ok=False)
        model_file = directory / "model.joblib"
        dump(model, model_file)
        (directory / "snapshot.json").write_bytes(encoded(snapshot))
        implementation = b"".join((ROOT / "machine_learning" / name).read_bytes()
                                  for name in ("series.py", "training.py", "artifacts.py", "__main__.py"))
        report = {**evaluation, "schema_version": 1, "model_version": identifier,
                  "trained_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                  "source_publication": snapshot["publication"], "trained_through": snapshot["through_month"],
                  "target": "Importe extendido mensual · SUM(ExtendedPrice)", "unit": "u.m. del origen",
                  "history": snapshot["history"], "dataset_sha256": snapshot["sha256"],
                  "implementation_sha256": sha256(implementation),
                  "environment": {"python": platform.python_version(), **versions},
                  "model_sha256": sha256(model_file.read_bytes())}
        validate_report(report)
        payload = encoded(report)
        (directory / "report.json").write_bytes(payload)
        self.root.mkdir(parents=True, exist_ok=True)
        # El puntero anterior continúa válido si falla cualquier escritura previa.
        with tempfile.NamedTemporaryFile(dir=self.root, prefix="current-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            file.write(encoded({"model_version": identifier, "report_sha256": sha256(payload)}))
            file.flush()
            os.fsync(file.fileno())
        try:
            os.replace(temporary, self.root / "current.json")
        finally:
            temporary.unlink(missing_ok=True)
        return report
