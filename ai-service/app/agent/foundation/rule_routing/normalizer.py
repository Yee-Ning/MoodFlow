from dataclasses import dataclass


_SYMBOL_TRANSLATION = {
    "，": ",",
    "。": ".",
    "！": "!",
    "？": "?",
    "；": ";",
    "：": ":",
    "（": "(",
    "）": ")",
    "【": "[",
    "】": "]",
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    "、": ",",
}
_REPEATABLE_NOISE = frozenset(",.!?;:")


@dataclass(frozen=True)
class NormalizedText:
    original: str
    text: str
    original_indices: tuple[int, ...]

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        if start < 0 or end <= start or end > len(self.original_indices):
            raise ValueError("normalized span is outside the source text")
        return self.original_indices[start], self.original_indices[end - 1] + 1


def _normalize_character(character: str) -> str:
    codepoint = ord(character)
    if 0xFF01 <= codepoint <= 0xFF5E:
        return chr(codepoint - 0xFEE0)
    if character == "\u3000":
        return " "
    return _SYMBOL_TRANSLATION.get(character, character)


def normalize_text(text: str) -> NormalizedText:
    """Normalize superficial typography while preserving source span mapping."""
    characters: list[str] = []
    indices: list[int] = []

    for source_index, source_character in enumerate(text):
        character = _normalize_character(source_character)
        if character.isspace():
            character = " "
            if not characters or characters[-1] == " ":
                continue
        if (
            character in _REPEATABLE_NOISE
            and characters
            and characters[-1] == character
        ):
            continue
        characters.append(character)
        indices.append(source_index)

    while characters and characters[0] == " ":
        characters.pop(0)
        indices.pop(0)
    while characters and characters[-1] == " ":
        characters.pop()
        indices.pop()

    return NormalizedText(
        original=text,
        text="".join(characters),
        original_indices=tuple(indices),
    )
