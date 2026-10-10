"""Relative niche classification by comment totals, not listening popularity."""

from dataclasses import dataclass


@dataclass(frozen=True)
class NicheConfig:
    min_comments: int
    max_comments: int

    def __post_init__(self):
        if (type(self.min_comments) is not int or type(self.max_comments) is not int
                or not 0 <= self.min_comments <= self.max_comments):
            raise ValueError("comment bounds must be ordered nonnegative integers")


# Initial policy, accepted by West after the eight-track live sample. These are
# human-chosen bounds, not percentiles or claims about the entire music catalog.
# Keep policy values outside classification and permit explicit caller injection.
INITIAL_NICHE_CONFIG = NicheConfig(min_comments=100, max_comments=10_000)


def get_niche_config() -> NicheConfig:
    return INITIAL_NICHE_CONFIG


def is_niche(comment_count, config: NicheConfig) -> bool:
    """Inclusive bounds; unknown/coerced values never qualify."""
    return type(comment_count) is int and config.min_comments <= comment_count <= config.max_comments


def rank_niche(candidates, k, *, config: NicheConfig | None = None):
    """Pure stable ascending ranking. No fallback, padding, or widened bounds."""
    if type(k) is not int or k < 0:
        raise ValueError("k must be a nonnegative integer")
    config = config if config is not None else get_niche_config()
    qualified = [candidate for candidate in candidates if is_niche(candidate.get("comment_count"), config)]
    return sorted(qualified, key=lambda candidate: candidate["comment_count"])[:k]
