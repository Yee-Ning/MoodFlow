from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, field_validator


class ChatContext(BaseModel):
    """Lightweight application-state references supplied by the frontend."""

    model_config = ConfigDict(extra="forbid")

    current_song_id: int | None = Field(default=None, gt=0)
    current_playlist_id: int | None = Field(default=None, gt=0)
    page: str | None = Field(default=None, max_length=200)

    @field_validator("page")
    @classmethod
    def normalize_page(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    context: ChatContext | None = None

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value


MessageText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000, pattern=r"\S"),
]
SearchQuery = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=r"\S"),
]


class _StructuredOutputBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: MessageText


class ChatOutput(_StructuredOutputBase):
    type: Literal["chat"]
    search_query: None


class MusicSearchOutput(_StructuredOutputBase):
    type: Literal["music_search"]
    search_query: SearchQuery


class MusicRecommendationOutput(_StructuredOutputBase):
    type: Literal["music_recommendation"]
    search_query: None


StructuredOutput = Annotated[
    ChatOutput | MusicSearchOutput | MusicRecommendationOutput,
    Field(discriminator="type"),
]
"""Strict structured payload returned by the LLM."""

STRUCTURED_OUTPUT_ADAPTER = TypeAdapter(StructuredOutput)

ChatResponse = StructuredOutput
