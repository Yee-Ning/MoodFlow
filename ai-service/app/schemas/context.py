from pydantic import BaseModel, ConfigDict, Field

from app.schemas.music import NonBlankText


class SongContext(BaseModel):
    """LLM-safe song metadata resolved through the Spring Boot backend."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(gt=0)
    name: NonBlankText
    artists: list[NonBlankText] = Field(min_length=1)
    album: str
    duration_ms: int = Field(ge=0)


class ResolvedContext(BaseModel):
    """Trusted runtime state after all available references are resolved."""

    model_config = ConfigDict(extra="forbid")

    current_song: SongContext | None = None
    current_playlist_id: int | None = Field(default=None, gt=0)
    page: str | None = None


class SpringSongData(BaseModel):
    """Subset of the Java SongData response needed by runtime context."""

    model_config = ConfigDict(extra="ignore")

    id: int = Field(gt=0)
    name: NonBlankText
    artists: list[NonBlankText] = Field(min_length=1)
    album: str
    duration: int = Field(ge=0)


class SpringSongDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: SpringSongData
