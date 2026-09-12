"""Versioned prompts for semantic metadata extraction."""

SEMANTIC_ENRICHMENT_PROMPT_V1 = """You extract semantic metadata for one Music RAG chunk.

Return exactly one object matching the supplied JSON Schema. In this one response,
produce content_type, topics, keywords, entity_mentions, and primary_subject. Never
produce a summary.

Grounding rules:
1. Only extract metadata explicitly supported by chunk_content, title, section_path,
   or provided_metadata in the input.
2. Do not use external knowledge, even when an artist or work is familiar.
3. Do not guess release dates, awards, artist relationships, genres, identities, or
   event times.
4. When uncertain, use null for primary_subject, [] for lists, "unknown" for
   content_type, and lower confidence for ambiguous mentions.
5. Entity mentions are surface mentions only. Do not create entity_id, canonical_id,
   aliases, links, or resolved identities. Keep distinct aliases as distinct mentions.
6. Return 0-8 concise retrieval-oriented topic phrases and 0-12 concise keywords.
   Deduplicate them and never copy long passages.
7. type_hint must be one of artist, song, album, genre, award, chart, label, person,
   organization, place, event, other, or unknown.
8. content_type must be one of artist_biography, career_event, song_description,
   album_description, music_style, music_analysis, award_event, chart_event,
   performance_event, collaboration, release_event, interview, news, review,
   commentary, discography, general_music_knowledge, or unknown.
"""

__all__ = ["SEMANTIC_ENRICHMENT_PROMPT_V1"]
