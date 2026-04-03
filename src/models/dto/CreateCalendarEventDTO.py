from pydantic import BaseModel


class CreateCalendarEventDTO(BaseModel):
    title: str
    description: str | None = None
    start_at: str  # ISO format: YYYY-MM-DDTHH:MM:SS
    end_at: str
    is_all_day: bool = False
    location: str | None = None
    meeting_url: str | None = None
