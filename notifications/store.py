"""Mutaciones limitadas a procedimientos ops; el DW mantiene acceso SELECT."""
import importlib
import json
import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

from backend.repository import ApiProblem

COMMANDS = {
    "record": "EXEC ops.usp_alert_record ?,?,?,?,?,?,?,?,?,?,?,?;",
    "claim": "EXEC ops.usp_alert_claim ?,?,?;",
    "deliver": "EXEC ops.usp_alert_deliver ?,?;",
    "retry": "EXEC ops.usp_alert_retry ?,?,?,?,?;",
    "read": "EXEC ops.usp_notification_read ?;",
}


class AlertStore:
    def __init__(self, settings):
        self.settings = settings

    def execute(self, command, parameters):
        sql = COMMANDS[command]
        connection = cursor = None
        try:
            pyodbc = importlib.import_module("pyodbc")
            connection = pyodbc.connect(self.settings.connection_string(), readonly=False, autocommit=True, timeout=self.settings.login_timeout)
            connection.timeout = self.settings.query_timeout
            cursor = connection.cursor()
            cursor.execute("SELECT DB_NAME();")
            if cursor.fetchone()[0].casefold() != self.settings.database.casefold():
                raise ValueError("Destino incorrecto.")
            cursor.execute(sql, *parameters)
            columns = [column[0].lower() for column in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        except Exception as exc:
            state = str(exc.args[0]) if exc.args else ""
            logging.getLogger("notifications").warning("Alert operation failed: operation=%s type=%s sqlstate=%s", command,
                type(exc).__name__, state if re.fullmatch(r"[A-Z0-9]{5}", state) else "unavailable")
            if re.search(r"\(51102\)", " ".join(str(arg) for arg in exc.args)):
                raise ApiProblem(409, "PUBLICATION_CHANGED", "El corte cambió durante la evaluación. Reintenta el ciclo de alertas.") from None
            raise ApiProblem(503, "NOTIFICATIONS_UNAVAILABLE", "No se pudo completar la operación del buzón. Revisa el script 005, permisos y conexión.") from None
        finally:
            for resource in (cursor, connection):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass

    def record(self, evaluated, publication, evaluation_id=None):
        payload = {**evaluated, "source_publication": publication}
        return self.execute("record", (str(evaluation_id or uuid4()), publication["id"], evaluated["period"],
            evaluated["condition"], evaluated["mode"], evaluated["current"], evaluated["previous"], evaluated["change_pct"],
            evaluated["threshold_pct"], evaluated["status"], evaluated["dedup_key"], json.dumps(payload, ensure_ascii=False)))[0]

    def claim(self, max_attempts, retry_now=False):
        rows = self.execute("claim", (str(uuid4()), max_attempts, retry_now))
        return rows[0] if rows else None

    def deliver(self, event):
        return self.execute("deliver", (str(event["event_id"]), str(event["lease_token"])))[0]

    def retry(self, event, code, max_attempts, delay):
        return self.execute("retry", (str(event["event_id"]), str(event["lease_token"]), code, max_attempts, delay))[0]

    def read(self, identifier):
        rows = self.execute("read", (str(identifier),))
        if not rows:
            raise ApiProblem(404, "NOTIFICATION_NOT_FOUND", "La notificación no existe.")
        stamp = rows[0]["read_at_utc"]
        if isinstance(stamp, datetime):
            stamp = stamp.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        return {"status": "READ", "notification_id": str(rows[0]["notification_id"]), "read_at_utc": stamp}
