import logging

import httpx
from pydantic import ValidationError

from app.config.settings import get_settings
from app.schemas.context import SpringUserIdentityResponse

logger = logging.getLogger(__name__)


class UserIdentityProvider:
    """Resolve the authenticated user from Spring's trusted server-side session."""

    async def resolve(self, session_cookie: str | None) -> str | None:
        if session_cookie is None:
            return None
        settings = get_settings()
        url = f"{str(settings.spring_boot_base_url).rstrip('/')}/api/user/profile"
        try:
            async with httpx.AsyncClient(
                timeout=settings.spring_boot_timeout_seconds,
                trust_env=False,
            ) as client:
                response = await client.get(url, headers={"Cookie": session_cookie})
            if response.status_code == 401:
                return None
            response.raise_for_status()
            payload = SpringUserIdentityResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError):
            logger.warning(
                "Authenticated user identity could not be resolved; memory disabled for request",
                exc_info=True,
            )
            return None
        if payload.code != 200:
            return None
        return str(payload.data.user_id)
