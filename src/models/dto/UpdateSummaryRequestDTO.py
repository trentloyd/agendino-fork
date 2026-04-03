from pydantic import BaseModel


class UpdateSummaryRequestDTO(BaseModel):
    title: str | None = None
    tags: list[str] | None = None
    summary: str | None = None
