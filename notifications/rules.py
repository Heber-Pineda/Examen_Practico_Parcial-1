from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext
import hashlib
import json

RULE_ID = "DEMO_SALES_MOM"


def month_start(period):
    result = date.fromisoformat(period + "-01")
    if len(period) != 7 or result.isoformat()[:7] != period:
        raise ValueError("Mes inválido; usar YYYY-MM.")
    return result


def shift_month(period, delta):
    value = month_start(period)
    year, month = divmod(value.year * 12 + value.month - 1 + delta, 12)
    return date(year, month + 1, 1).isoformat()[:7]


def evaluate(rows, period, threshold=Decimal("-10"), mode="CONFIGURED"):
    month_start(period)
    threshold = Decimal(threshold).quantize(Decimal("0.000001"))
    if not threshold.is_finite() or abs(threshold) > 1000000 or mode not in {"CONFIGURED", "FORCED_DEMO_ON", "FORCED_DEMO_OFF"}:
        raise ValueError("Configuración de regla inválida.")
    values = {row["period"]: row["amount"] for row in rows}
    current, previous = values.get(period), values.get(shift_month(period, -1))
    change = None
    if current is not None and previous is not None and Decimal(previous) > 0:
        with localcontext() as ctx:
            ctx.prec = 60
            change = ((Decimal(current) - Decimal(previous)) * 100 / Decimal(previous)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        if mode != "CONFIGURED":
            threshold = change + (1 if mode == "FORCED_DEMO_ON" else -1)
    status = "INSUFFICIENT_DATA" if change is None else "TRIGGERED" if change <= threshold else "NOT_TRIGGERED"
    condition = "100 × (importe extendido mensual − mes anterior) / mes anterior <= umbral; referencia positiva; precisión 6 decimales."
    content = {"rule_id": RULE_ID, "rule_version": "1", "period": period, "current": None if current is None else str(current),
               "previous": None if previous is None else str(previous), "change_pct": None if change is None else str(change),
               "threshold_pct": str(threshold), "mode": mode, "condition": condition, "is_demo": True, "status": status}
    key = json.dumps({k: v for k, v in content.items() if k not in {"status", "condition", "is_demo"}}, sort_keys=True)
    content["dedup_key"] = hashlib.sha256(key.encode()).hexdigest() if status == "TRIGGERED" else None
    content["title"] = f"Demostración académica · ventas de {period}"
    content["body"] = (f"Variación {change}% <= umbral {threshold}%. Importe extendido {current} u.m.; mes anterior {previous} u.m. "
                       + ("Umbral ajustado para demostrar activación con datos reales." if mode != "CONFIGURED" else "Regla académica configurable; no es una política comercial aprobada."))
    return content
