import logging

from app.context.music import MusicContextProvider
from app.schemas.chat import ChatContext
from app.schemas.context import ResolvedContext

logger = logging.getLogger(__name__)


async def build_context(
    context: ChatContext | None,
    provider: MusicContextProvider | None = None,
) -> ResolvedContext:
    """Resolve frontend references without allowing context failure to fail chat."""
    if context is None:
        return ResolvedContext()

    current_song = None
    if context.current_song_id is not None:
        try:
            current_song = await (provider or MusicContextProvider()).get_song(
                context.current_song_id
            )
        except Exception:
            logger.warning(
                "Current song context unavailable; continuing without it: song_id=%d",
                context.current_song_id,
                exc_info=True,
            )

    return ResolvedContext(
        current_song=current_song,
        current_playlist_id=context.current_playlist_id,
        page=context.page,
    )
