from datetime import datetime
from pydantic import BaseModel
from typing import Optional


class CreateManualActionItemDTO(BaseModel):
    title: str
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    priority: str = "medium"
    assigned_to: Optional[str] = None
    recording_id: Optional[int] = None
    meeting_title: Optional[str] = None
    meeting_date: Optional[datetime] = None