import json
from datetime import datetime


class DBDailyRecap:
    def __init__(
        self,
        id: int | None,
        date: str,
        title: str | None = None,
        highlights: list[str] | None = None,
        recap: str | None = None,
        action_items: list[str] | None = None,
        blockers: list[str] | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ):
        self.id = id
        self.date = date
        self.title = title
        self.highlights = highlights or []
        self.recap = recap
        self.action_items = action_items or []
        self.blockers = blockers or []
        self.created_at = created_at
        self.updated_at = updated_at

    @staticmethod
    def from_dict(data) -> "DBDailyRecap":
        def _parse_json_list(raw) -> list[str]:
            if not raw:
                return []
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                return []

        created = data["created_at"] if "created_at" in data.keys() else None
        updated = data["updated_at"] if "updated_at" in data.keys() else None

        return DBDailyRecap(
            id=data["id"],
            date=data["date"],
            title=data["title"] if "title" in data.keys() else None,
            highlights=_parse_json_list(data["highlights"] if "highlights" in data.keys() else None),
            recap=data["recap"] if "recap" in data.keys() else None,
            action_items=_parse_json_list(data["action_items"] if "action_items" in data.keys() else None),
            blockers=_parse_json_list(data["blockers"] if "blockers" in data.keys() else None),
            created_at=datetime.fromisoformat(created) if created else None,
            updated_at=datetime.fromisoformat(updated) if updated else None,
        )

    @staticmethod
    def from_recap_dict(date: str, recap: dict) -> "DBDailyRecap":
        """Create a DBDailyRecap from the AI service output dict."""
        return DBDailyRecap(
            id=None,
            date=date,
            title=recap.get("title"),
            highlights=recap.get("highlights", []),
            recap=recap.get("recap"),
            action_items=recap.get("action_items", []),
            blockers=recap.get("blockers", []),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "date": self.date,
            "title": self.title,
            "highlights": self.highlights,
            "recap": self.recap,
            "action_items": self.action_items,
            "blockers": self.blockers,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
