from pydantic import BaseModel


class GenerateTasksRequestDTO(BaseModel):
    summary_id: int
