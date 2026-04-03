from pydantic import BaseModel


class UpdateCalendarEventDTO(BaseModel):
    title: str | None = None
    description: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    is_all_day: bool | None = None
    location: str | None = None
    meeting_url: str | None = None
