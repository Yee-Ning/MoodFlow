from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


NonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, pattern=r"\S"),
]


class SearchMusicArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: NonBlankText = Field(
        max_length=100,
        description="Song name, artist name, album name, or other concrete music keyword.",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of matching tracks to return.",
    )


class MusicTrack(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: int = Field(gt=0)
    name: NonBlankText
    artists: list[NonBlankText] = Field(min_length=1)
    album: str
    cover_url: str = Field(alias="coverUrl")
    duration: int = Field(ge=0)

    @field_validator("album", "cover_url")
    @classmethod
    def strip_optional_text(cls, value: str) -> str:
        return value.strip()


class MusicSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: NonBlankText
    limit: int = Field(ge=1, le=50)
    songs: list[MusicTrack]


class SpringMusicSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: list[MusicTrack]
