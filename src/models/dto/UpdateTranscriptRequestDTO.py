from pydantic import BaseModel


class UpdateTranscriptRequestDTO(BaseModel):
    transcript: str
