from datetime import datetime
from pydantic import BaseModel
from typing import Optional


class CreateActionItemDTO(BaseModel):
    task_id: int
    title: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    priority: str = "medium"
    assigned_to: Optional[str] = None