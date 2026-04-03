from pydantic import BaseModel


class ProactorAnalyzeRequestDTO(BaseModel):
    start_date: str  # YYYY-MM-DD
    end_date: str  # YYYY-MM-DD
