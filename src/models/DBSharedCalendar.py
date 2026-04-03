from datetime import datetime


class DBSharedCalendar:
    def __init__(
        self,
        id: int | None,
        name: str = "",
        ical_url: str = "",
        color: str = "#0d6efd",
        is_enabled: bool = True,
        sync_interval_minutes: int = 30,
        last_synced_at: str | None = None,
        last_error: str | None = None,
        created_at: datetime | None = None,
        # Hydrated
        event_count: int = 0,
    ):
        self.id = id
        self.name = name
        self.ical_url = ical_url
        self.color = color
        self.is_enabled = is_enabled
        self.sync_interval_minutes = sync_interval_minutes
        self.last_synced_at = last_synced_at
        self.last_error = last_error
        self.created_at = created_at
        self.event_count = event_count

    @staticmethod
    def from_dict(data):
        created = data["created_at"] if "created_at" in data.keys() else None
        return DBSharedCalendar(
            id=data["id"],
            name=data["name"],
            ical_url=data["ical_url"],
            color=data["color"] if "color" in data.keys() else "#0d6efd",
            is_enabled=bool(data["is_enabled"]) if "is_enabled" in data.keys() else True,
            sync_interval_minutes=int(data["sync_interval_minutes"]) if "sync_interval_minutes" in data.keys() else 30,
            last_synced_at=data["last_synced_at"] if "last_synced_at" in data.keys() else None,
            last_error=data["last_error"] if "last_error" in data.keys() else None,
            created_at=datetime.fromisoformat(created) if created else None,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "ical_url": self.ical_url,
            "color": self.color,
            "is_enabled": self.is_enabled,
            "sync_interval_minutes": self.sync_interval_minutes,
            "last_synced_at": self.last_synced_at,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "event_count": self.event_count,
        }
