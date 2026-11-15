"""Predicciones batch publicadas: lectura JSON, sin entrenar ni cargar pickle."""

from machine_learning.artifacts import ArtifactError, ReportStore
from .repository import ApiProblem


class ForecastService:
    def __init__(self, analytics, store=None):
        self.analytics = analytics
        self.store = store if store is not None else ReportStore()

    def predictions(self, expected_publication=None):
        try:
            report = self.store.load()
        except ArtifactError:
            raise ApiProblem(503, "ML_ARTIFACT_INVALID", "El modelo guardado no está disponible. Revisar o repetir el entrenamiento.") from None
        if report is None:
            return {"status": "NOT_TRAINED", "message": "Todavía no hay un modelo entrenado. Ejecuta el entrenamiento desde Python."}
        response = self.analytics.consistent(lambda reader: {"model": report}, expected_publication)
        stale = response["publication"]["id"] != report["source_publication"]["id"]
        return {"status": "STALE" if stale else "READY", **response}

    def metrics(self, expected_publication=None):
        result = self.predictions(expected_publication)
        if result["status"] == "NOT_TRAINED":
            return result
        model = result["model"]
        return {"status": result["status"], "publication": result["publication"],
                "model_version": model["model_version"], "trained_at_utc": model["trained_at_utc"],
                "source_publication": model["source_publication"], "selected_method": model["selected_method"],
                "split": model["split"], "metrics": model["metrics"],
                "validation_candidates": model["validation_candidates"], "selection_rule": model["selection_rule"]}
