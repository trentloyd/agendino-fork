from pydantic import BaseModel


class UpdateRecordingRequestDTO(BaseModel):
    recorded_at: str | None = None
    duration: int | None = None
