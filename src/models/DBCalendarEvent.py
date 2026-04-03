from datetime import datetime


class DBCalendarEvent:
    def __init__(
        self,
        id: int | None,
        provider: str = "local",
        external_id: str | None = None,
        shared_calendar_id: int | None = None,
        title: str = "",
        description: str | None = None,
        start_at: str = "",
        end_at: str = "",
        is_all_day: bool = False,
        location: str | None = None,
        meeting_url: str | None = None,
        status: str = "confirmed",
        created_at: datetime | None = None,
        # Hydrated fields (not stored directly)
        linked_recordings: list | None = None,
        calendar_color: str | None = None,
        calendar_name: str | None = None,
    ):
        self.id = id
        self.provider = provider
        self.external_id = external_id
        self.shared_calendar_id = shared_calendar_id
        self.title = title
        self.description = description
        self.start_at = start_at
        self.end_at = end_at
        self.is_all_day = is_all_day
        self.location = location
        self.meeting_url = meeting_url
        self.status = status
        self.created_at = created_at
        self.linked_recordings = linked_recordings or []
        self.calendar_color = calendar_color
        self.calendar_name = calendar_name

    @staticmethod
    def from_dict(data):
        created = data["created_at"] if "created_at" in data.keys() else None
        return DBCalendarEvent(
            id=data["id"],
            provider=data["provider"] if "provider" in data.keys() else "local",
            external_id=data["external_id"] if "external_id" in data.keys() else None,
            shared_calendar_id=data["shared_calendar_id"] if "shared_calendar_id" in data.keys() else None,
            title=data["title"],
            description=data["description"] if "description" in data.keys() else None,
            start_at=data["start_at"],
            end_at=data["end_at"],
            is_all_day=bool(data["is_all_day"]) if "is_all_day" in data.keys() else False,
            location=data["location"] if "location" in data.keys() else None,
            meeting_url=data["meeting_url"] if "meeting_url" in data.keys() else None,
            status=data["status"] if "status" in data.keys() else "confirmed",
            created_at=datetime.fromisoformat(created) if created else None,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "provider": self.provider,
            "external_id": self.external_id,
            "shared_calendar_id": self.shared_calendar_id,
            "title": self.title,
            "description": self.description,
            "start_at": self.start_at,
            "end_at": self.end_at,
            "is_all_day": self.is_all_day,
            "location": self.location,
            "meeting_url": self.meeting_url,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "calendar_color": self.calendar_color,
            "calendar_name": self.calendar_name,
            "linked_recordings": [
                {
                    "recording_id": lr["recording_id"],
                    "name": lr["name"],
                    "label": lr["label"],
                    "link_source": lr.get("link_source", "manual"),
                    "has_transcript": lr.get("has_transcript", False),
                    "has_summary": lr.get("has_summary", False),
                    "summary_id": lr.get("summary_id"),
                    "summary_title": lr.get("summary_title"),
                    "summary_tags": lr.get("summary_tags", []),
                    "summary_text": lr.get("summary_text"),
                }
                for lr in self.linked_recordings
            ],
        }
