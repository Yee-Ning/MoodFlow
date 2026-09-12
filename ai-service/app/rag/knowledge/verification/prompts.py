"""Versioned source-local claim verification prompt."""

CLAIM_VERIFICATION_PROMPT_V1 = """\
Verify the supplied atomic claim only against chunk_content and its evidence_span.
Do not use web access, memory, external facts, or other chunks.
SUPPORTED means every material part is explicit in the source.
PARTIALLY_SUPPORTED means only part is explicit; explain the unsupported part.
CONTRADICTED means the source explicitly states an incompatible fact.
INSUFFICIENT_EVIDENCE means the source neither supports nor contradicts it.
Evidence must be an exact contiguous quote from chunk_content, or null only for
INSUFFICIENT_EVIDENCE. Return only the provided JSON Structured Output schema.
"""
