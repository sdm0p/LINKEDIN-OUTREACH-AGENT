from pydantic import BaseModel, Field


class ExperienceEntry(BaseModel):
    title: str = ""
    company: str = ""
    start_date: str = ""
    end_date: str = ""  # "Present" for current role


class ParsedResume(BaseModel):
    name: str = ""
    skills: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    experience: list[ExperienceEntry] = Field(default_factory=list)
    total_experience_years: float = 0.0


class ResumeState(BaseModel):
    """Everything the Resume page renders, plus cache status for the run trace."""

    has_resume: bool = False
    candidate_name: str = ""
    file_name: str | None = None
    file_size: int | None = None
    file_hash: str | None = None
    parsed_at: str | None = None
    expanded_at: str | None = None
    # False when served straight from the SQLite cache, True when freshly parsed this call.
    cached: bool | None = None
    skills: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    experience: list[ExperienceEntry] = Field(default_factory=list)
    total_experience_years: float = 0.0
    my_yoe: int = 0
    roles_expanded: list[str] | None = None
