from datetime import datetime


class DBSummary:
    def __init__(
        self,
        id: int,
        recording_id: int,
        recording_name: str,
        version: int,
        summary: str,
        title: str | None = None,
        tags: str | None = None,
        prompt_id: str | None = None,
        notion_url: str | None = None,
        created_at: datetime | None = None,
    ):
        self.id = id
        self.recording_id = recording_id
        self.recording_name = recording_name
        self.version = version
        self.summary = summary
        self.title = title
        self.tags = tags
        self.prompt_id = prompt_id
        self.notion_url = notion_url
        self.created_at = created_at

    @staticmethod
    def from_dict(data):
        created = data["created_at"] if "created_at" in data.keys() else None
        return DBSummary(
            id=data["id"],
            recording_id=data["recording_id"],
            recording_name=data["recording_name"],
            version=data["version"],
            summary=data["summary"],
            title=data["title"] if "title" in data.keys() else None,
            tags=data["tags"] if "tags" in data.keys() else None,
            prompt_id=data["prompt_id"] if "prompt_id" in data.keys() else None,
            notion_url=data["notion_url"] if "notion_url" in data.keys() else None,
            created_at=datetime.fromisoformat(created) if created else None,
        )
