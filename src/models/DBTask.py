from datetime import datetime


class DBTask:
    def __init__(
        self,
        id: int | None,
        summary_id: int,
        title: str,
        description: str | None = None,
        parent_task_id: int | None = None,
        status: str = "open",
        created_at: datetime | None = None,
        subtasks: list["DBTask"] | None = None,
    ):
        self.id = id
        self.summary_id = summary_id
        self.title = title
        self.description = description
        self.parent_task_id = parent_task_id
        self.status = status
        self.created_at = created_at
        self.subtasks = subtasks or []

    @staticmethod
    def from_dict(data):
        created = data["created_at"] if "created_at" in data.keys() else None
        return DBTask(
            id=data["id"],
            summary_id=data["summary_id"],
            title=data["title"],
            description=data["description"] if "description" in data.keys() else None,
            parent_task_id=data["parent_task_id"] if "parent_task_id" in data.keys() else None,
            status=data["status"] if "status" in data.keys() else "open",
            created_at=datetime.fromisoformat(created) if created else None,
        )

    def to_dict(self) -> dict:
        result = {
            "id": self.id,
            "summary_id": self.summary_id,
            "title": self.title,
            "description": self.description,
            "parent_task_id": self.parent_task_id,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "subtasks": [s.to_dict() for s in self.subtasks],
        }
        return result
