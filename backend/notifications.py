from datetime import datetime, timezone
import json

from .repository import SqlRepository
from notifications.store import AlertStore


def utc(value):
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if isinstance(value, datetime) else value


class NotificationService:
    def __init__(self, settings, repository=None, store=None):
        self.repository = repository if repository is not None else SqlRepository(settings)
        self.store = store if store is not None else AlertStore(settings)

    def list(self, limit=30):
        with self.repository.session() as reader:
            installed = reader.query("SELECT OBJECT_ID(N'ops.notification', N'U') AS object_id;")[0]["object_id"]
            if installed is None:
                return {"status": "NOT_READY", "message": "Ejecutar el script 005 y conceder permisos del buzón.", "items": [], "unread_count": 0}
            count = reader.query("SELECT COUNT_BIG(*) AS total FROM ops.notification WHERE read_at_utc IS NULL;")[0]["total"]
            rows = reader.query("SELECT TOP (?) n.notification_id,n.title,n.body,n.delivered_at_utc,n.read_at_utc,"
                                "e.source_publication,e.is_demo,e.payload_json,e.delivery_status "
                                "FROM ops.notification n JOIN ops.alert_event e ON e.event_id=n.event_id "
                                "ORDER BY n.delivered_at_utc DESC,n.notification_id DESC;", (limit,))
        items = []
        for row in rows:
            payload = json.loads(row.pop("payload_json"))
            items.append({**{key: utc(value) for key, value in row.items()},
                          "notification_id": str(row["notification_id"]), "source_publication": str(row["source_publication"]),
                          "period": payload["period"], "change_pct": payload["change_pct"],
                          "threshold_pct": payload["threshold_pct"], "mode": payload["mode"]})
        return {"status": "READY", "items": items, "unread_count": int(count)}

    def mark_read(self, identifier):
        return self.store.read(identifier)
