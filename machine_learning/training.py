"""Ridge de tres variables de calendario frente a referencia estacional.

Selección exclusivamente con validación; prueba con parámetros congelados.
En cada origen se ajusta de nuevo usando solo meses ya observados.
"""

from decimal import Decimal, ROUND_HALF_UP, localcontext
import math

from .series import add_months, month_date

HORIZON = 3
ALPHAS = (0.1, 1.0, 10.0)
RULES_VERSION = "monthly-ridge-v1"


def build_model(alpha):
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(StandardScaler(), Ridge(alpha=alpha, solver="svd"))


def features(period, index):
    month = month_date(period).month
    return [float(index), math.sin(2 * math.pi * month / 12), math.cos(2 * math.pi * month / 12)]


def money(value):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("La predicción no es finita.")
    with localcontext() as ctx:
        ctx.prec = 80
        return format(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


def scores(actual, predicted):
    if not actual or len(actual) != len(predicted):
        raise ValueError("La evaluación no tiene pares completos.")
    errors = [float(a) - float(p) for a, p in zip(actual, predicted, strict=True)]
    if not all(math.isfinite(e) for e in errors):
        raise ValueError("La evaluación contiene valores no finitos.")
    return {"mae": math.fsum(abs(e) for e in errors) / len(errors),
            "rmse": math.sqrt(math.fsum(e * e for e in errors) / len(errors))}


def fit(history, alpha, factory):
    model = factory(alpha)
    model.fit([features(m.period, i) for i, m in enumerate(history)], [float(m.amount) for m in history])
    return model


def backtest(history, start, end, alpha, factory):
    actual, predicted, baseline, rows = [], [], [], []
    for origin in range(start, end, HORIZON):
        # Copia del prefijo: ni el escalador ni Ridge ven el bloque por predecir.
        model = fit(history[:origin], alpha, factory)
        targets = history[origin:min(origin + HORIZON, end)]
        estimates = list(model.predict([features(m.period, origin + offset) for offset, m in enumerate(targets)]))
        if len(estimates) != len(targets):
            raise ValueError("El modelo no produjo el horizonte solicitado.")
        for offset, (target, estimate) in enumerate(zip(targets, estimates, strict=True)):
            reference = history[origin + offset - 12].amount
            actual.append(float(target.amount)); predicted.append(float(estimate)); baseline.append(float(reference))
            rows.append({"period": target.period, "origin": history[origin - 1].period,
                         "actual": money(target.amount), "ridge": money(estimate), "seasonal_naive": money(reference)})
    return {"ridge": scores(actual, predicted), "seasonal_naive": scores(actual, baseline), "predictions": rows}


def train(history, factory=build_model):
    n = len(history)
    if n < 36 or any(history[i].period != add_months(history[i - 1].period, 1) for i in range(1, n)):
        raise ValueError("Se requieren al menos 36 meses continuos.")
    train_end, validation_end = n - 12, n - 6
    candidates = [(alpha, backtest(history, train_end, validation_end, alpha, factory)) for alpha in ALPHAS]
    alpha, validation = min(candidates, key=lambda item: (item[1]["ridge"]["mae"], item[0]))
    selected = "ridge" if validation["ridge"]["mae"] < validation["seasonal_naive"]["mae"] else "seasonal_naive"
    # La selección queda cerrada ANTES de observar las métricas de prueba.
    test = backtest(history, validation_end, n, alpha, factory)
    model = fit(history, alpha, factory)
    periods = [add_months(history[-1].period, i) for i in range(1, HORIZON + 1)]
    estimates = list(model.predict([features(period, n + i) for i, period in enumerate(periods)]))
    if len(estimates) != HORIZON:
        raise ValueError("Horizonte incompleto.")
    forecast = [{"period": period, "ridge": money(estimate),
                 "seasonal_naive": money(history[n + i - 12].amount), "is_estimate": True}
                for i, (period, estimate) in enumerate(zip(periods, estimates, strict=True))]
    for row in forecast:
        row["amount"] = row[selected]
    def split(first, last):
        return {"from": history[first].period, "to": history[last - 1].period, "months": last - first}
    return model, {"rules_version": RULES_VERSION, "horizon": HORIZON, "selected_method": selected,
        "ridge_alpha": alpha, "features": ["month_index", "sin_month_12", "cos_month_12"],
        "split": {"train": split(0, train_end), "validation": split(train_end, validation_end),
                  "test": split(validation_end, n)},
        "evaluation_protocol": "Expanding window; two 3-month blocks per segment; refit only on observed prefixes.",
        "selection_rule": "Lowest validation MAE; baseline wins ties; test never selects method or alpha.",
        "validation_candidates": [{"alpha": a, "ridge": result["ridge"]} for a, result in candidates],
        "metrics": {"validation": validation, "test": test},
        "forecast": forecast,
        "limitations": ["Solo una serie mensual global; los filtros de ventas no modifican este modelo.",
                        "El horizonte parte del cierre histórico, no del calendario actual.",
                        "Serie corta: MAE/RMSE no garantizan precisión futura; no se calculan intervalos de confianza.",
                        "Se conservan signos e impuestos del importe extendido; no se aplican piso cero ni conversiones monetarias."]}
