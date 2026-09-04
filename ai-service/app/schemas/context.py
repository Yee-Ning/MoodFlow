from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.music import NonBlankText


class SongContext(BaseModel):
    """Trusted current-song data aggregated by the Spring Boot backend."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    song_id: int = Field(alias="songId", gt=0)
    name: NonBlankText
    artist: NonBlankText
    album: str
    original_lyrics: str = Field(alias="originalLyrics")
    translated_lyrics: str = Field(alias="translatedLyrics")
    lyrics_status: Literal["AVAILABLE", "NO_LYRICS", "UNAVAILABLE"] = Field(
        alias="lyricsStatus"
    )

    @field_validator("album", "original_lyrics", "translated_lyrics")
    @classmethod
    def normalize_optional_text(cls, value: str) -> str:
        return value.strip()


class ResolvedContext(BaseModel):
    """Trusted runtime state after all available references are resolved."""

    model_config = ConfigDict(extra="forbid")

    current_song: SongContext | None = None
    current_playlist_id: int | None = Field(default=None, gt=0)
    page: str | None = None


class SpringCurrentSongContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: SongContext
