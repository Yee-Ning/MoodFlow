import logging

import httpx
from pydantic import ValidationError

from app.config.settings import get_settings
from app.schemas.context import SongContext, SpringCurrentSongContextResponse

logger = logging.getLogger(__name__)


class MusicContextProviderError(RuntimeError):
    """Raised when current-song context cannot be resolved through Java."""


class MusicContextProvider:
    async def get_song(self, song_id: int) -> SongContext:
        settings = get_settings()
        url = (
            f"{str(settings.spring_boot_base_url).rstrip('/')}"
            f"/api/songs/{song_id}/context"
        )

        try:
            async with httpx.AsyncClient(
                timeout=settings.spring_boot_timeout_seconds
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise MusicContextProviderError(
                "Spring Boot song detail request timed out"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise MusicContextProviderError(
                f"Spring Boot song detail failed with HTTP "
                f"{exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise MusicContextProviderError(
                "Cannot connect to Spring Boot song detail service"
            ) from exc

        try:
            payload = SpringCurrentSongContextResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise MusicContextProviderError(
                "Spring Boot song detail returned invalid data"
            ) from exc

        if payload.code != 200:
            raise MusicContextProviderError(
                f"Spring Boot song detail failed with business code {payload.code}"
            )

        song = payload.data
        if song.song_id != song_id:
            raise MusicContextProviderError(
                "Spring Boot song context returned a mismatched song id"
            )
        logger.info(
            "Resolved current song through Spring Boot: song_id=%d",
            song.song_id,
        )
        return song
