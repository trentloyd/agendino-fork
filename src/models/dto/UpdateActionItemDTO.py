from datetime import datetime
from pydantic import BaseModel
from typing import Optional


class UpdateActionItemDTO(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    assigned_to: Optional[str] = None