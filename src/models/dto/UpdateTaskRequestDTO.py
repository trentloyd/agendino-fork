from pydantic import BaseModel


class UpdateTaskRequestDTO(BaseModel):
    status: str | None = None
    title: str | None = None
    description: str | None = None
