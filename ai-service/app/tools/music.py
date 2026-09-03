import logging

import httpx
from pydantic import ValidationError

from app.config.settings import get_settings
from app.schemas.music import (
    MusicSearchResult,
    SearchMusicArgs,
    SpringMusicSearchResponse,
)

logger = logging.getLogger(__name__)


class MusicSearchError(RuntimeError):
    """Raised when the Spring Boot music search cannot return trusted data."""


async def search_music(keyword: str, limit: int = 10) -> MusicSearchResult:
    search_input = SearchMusicArgs(keyword=keyword, limit=limit)
    settings = get_settings()
    url = f"{str(settings.spring_boot_base_url).rstrip('/')}/api/music/search"

    logger.info(
        "Searching music through Spring Boot: keyword=%r limit=%d",
        search_input.keyword,
        search_input.limit,
    )

    try:
        async with httpx.AsyncClient(
            timeout=settings.spring_boot_timeout_seconds
        ) as client:
            response = await client.get(
                url,
                params={
                    "keyword": search_input.keyword,
                    "limit": search_input.limit,
                },
            )
            response.raise_for_status()
    except httpx.TimeoutException as exc:
        logger.warning(
            "Spring Boot music search timed out: keyword=%r",
            search_input.keyword,
        )
        raise MusicSearchError("Spring Boot music search timed out") from exc
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Spring Boot music search returned HTTP %d: keyword=%r",
            exc.response.status_code,
            search_input.keyword,
        )
        raise MusicSearchError(
            f"Spring Boot music search failed with HTTP {exc.response.status_code}"
        ) from exc
    except httpx.RequestError as exc:
        logger.error(
            "Cannot connect to Spring Boot music search: keyword=%r",
            search_input.keyword,
        )
        raise MusicSearchError("Cannot connect to Spring Boot music search") from exc

    try:
        payload = SpringMusicSearchResponse.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        logger.exception(
            "Spring Boot music search returned invalid data: keyword=%r",
            search_input.keyword,
        )
        raise MusicSearchError("Spring Boot music search returned invalid data") from exc

    if payload.code != 200:
        logger.error(
            "Spring Boot music search returned business code %d: keyword=%r",
            payload.code,
            search_input.keyword,
        )
        raise MusicSearchError(
            f"Spring Boot music search failed with business code {payload.code}"
        )

    logger.info(
        "Spring Boot music search completed: keyword=%r songs=%d",
        search_input.keyword,
        len(payload.data),
    )
    return MusicSearchResult(
        keyword=search_input.keyword,
        limit=search_input.limit,
        songs=payload.data,
    )
