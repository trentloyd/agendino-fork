from datetime import datetime


class DBActionItem:
    def __init__(
        self,
        id: int | None,
        task_id: int,
        recording_id: int,
        summary_id: int,
        title: str,
        description: str | None = None,
        due_date: datetime | None = None,
        priority: str = "medium",
        status: str = "pending",
        archived: bool = False,
        assigned_to: str | None = None,
        meeting_title: str | None = None,
        meeting_date: datetime | None = None,
        created_at: datetime | None = None,
        completed_at: datetime | None = None,
        archived_at: datetime | None = None,
    ):
        self.id = id
        self.task_id = task_id
        self.recording_id = recording_id
        self.summary_id = summary_id
        self.title = title
        self.description = description
        self.due_date = due_date
        self.priority = priority
        self.status = status
        self.archived = archived
        self.assigned_to = assigned_to
        self.meeting_title = meeting_title
        self.meeting_date = meeting_date
        self.created_at = created_at
        self.completed_at = completed_at
        self.archived_at = archived_at

    @staticmethod
    def from_dict(data):
        return DBActionItem(
            id=data.get("id"),
            task_id=data["task_id"],
            recording_id=data["recording_id"],
            summary_id=data["summary_id"],
            title=data["title"],
            description=data.get("description"),
            due_date=datetime.fromisoformat(data["due_date"]) if data.get("due_date") else None,
            priority=data.get("priority", "medium"),
            status=data.get("status", "pending"),
            archived=data.get("archived", False),
            assigned_to=data.get("assigned_to"),
            meeting_title=data.get("meeting_title"),
            meeting_date=datetime.fromisoformat(data["meeting_date"]) if data.get("meeting_date") else None,
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            archived_at=datetime.fromisoformat(data["archived_at"]) if data.get("archived_at") else None,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "recording_id": self.recording_id,
            "summary_id": self.summary_id,
            "title": self.title,
            "description": self.description,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "priority": self.priority,
            "status": self.status,
            "archived": self.archived,
            "assigned_to": self.assigned_to,
            "meeting_title": self.meeting_title,
            "meeting_date": self.meeting_date.isoformat() if self.meeting_date else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "archived_at": self.archived_at.isoformat() if self.archived_at else None,
        }