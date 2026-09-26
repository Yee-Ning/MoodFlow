"""Capture two public source contexts into an independent input-only freeze."""

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import subprocess

from bs4 import BeautifulSoup
import requests

from app.agent.foundation.subgraphs.worldline.source_context import RelationSourceContext
from evals.semantic_relations_models import file_hash, load_suite


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "evals/semantic_relations_v1_review_r6"
OUTPUT = ROOT / "evals/semantic_relations_context_inputs_v1"
REPORT = ROOT / "evals/reports/semantic_relations_v1_influence_context_repair"
PREVIOUS = ROOT / "evals/reports/semantic_relations_v1_conflict_repair_r6"
# Capture selectors identify source documents, never runtime entity rules.
CAPTURES = {
    "source:r3:guardian-cluster-stockhausen": "article_sentence",
    "source:r3:indieeye-cluster-stockhausen-denial": "interview_answer",
}


def write_json(path, payload):
    path.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode())


def segment(content, role, text):
    assert content.count(text) == 1
    start = content.index(text)
    return {"role": role, "exact_span": text, "start": start, "end": start + len(text)}


def build():
    if OUTPUT.exists() or REPORT.exists():
        raise ValueError("refuse_overwriting_context_version_or_report")
    _, inputs, sources, manifest = load_suite(BASE)
    protection = json.loads((PREVIOUS / "protected_artifacts.json").read_text(encoding="utf-8"))
    protected = dict(protection["files"])
    for path in PREVIOUS.iterdir():
        if path.is_file():
            protected[path.relative_to(ROOT).as_posix()] = file_hash(path)
    assert all(file_hash(ROOT / name) == digest for name, digest in protected.items())
    scope = json.loads((PREVIOUS / "scope_validation.json").read_text(encoding="utf-8"))
    runtime_before = {**scope["runtime_after"], **scope["new_runtime_files"]}
    records, audit = [], []
    for replay in inputs.values():
        documents = []
        for doc in replay.documents:
            source_id = doc.metadata["snapshot_source_id"]
            role = CAPTURES.get(source_id)
            if role is None:
                documents.append(doc)
                continue
            response = requests.get(str(doc.url), timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "html.parser")
            paragraphs = [p.get_text(" ", strip=True) for p in soup.select("p")]
            claim = doc.metadata["supported_claims"][0]
            exact = claim["evidence_span"]
            matches = [i for i, text in enumerate(paragraphs) if exact in text]
            assert len(matches) == 1, "source exact span moved or ambiguous"
            i = matches[0]
            if role == "article_sentence":
                paragraph = paragraphs[i]
                begin = paragraph.rfind(". ", 0, paragraph.index(exact)) + 2
                assert begin >= 2
                content = paragraph[begin:paragraph.index(exact) + len(exact)]
                parts = [segment(content, role, content)]
                locator = f"DOM p[{i}] (zero-based): complete sentence containing original claim span"
            else:
                title = soup.select_one("h1").get_text(" ", strip=True)
                assert title == doc.title
                # Nearest nonempty preceding paragraph is the original question.
                question = next(text for text in reversed(paragraphs[:i]) if text.strip())
                assert question.endswith("?") and paragraphs[i] == exact
                intro = next(text for text in paragraphs if text.startswith("Intervista rilasciata"))
                content = "\n\n".join([title, intro, question, exact])
                parts = [segment(content, "speaker_binding", title),
                         segment(content, "interview_question", question), segment(content, role, exact)]
                locator = f"DOM h1; interview introduction; p[{i-1}] question immediately preceding p[{i}] answer"
            context_id = "context:" + sha256((source_id + content).encode()).hexdigest()[:24]
            record = RelationSourceContext.model_validate({
                "context_id": context_id, "source_id": source_id,
                "document_id": doc.id, "chunk_id": doc.metadata["chunk_id"], "claim_id": claim["claim_id"],
                "source_url": str(doc.url), "publisher": doc.source, "title": doc.title,
                "locator": locator, "captured_at": datetime.now(UTC).isoformat(),
                "response_sha256": sha256(response.content).hexdigest(),
                "source_content_sha256": sources[source_id].content_hash,
                "content": content, "content_sha256": sha256(content.encode()).hexdigest(),
                "anchor": segment(content, role, exact), "segments": parts,
            })
            assert record.bound_to(doc, claim["claim_id"], exact)
            records.append(record)
            documents.append(doc.model_copy(update={"metadata": {
                **doc.metadata, "relation_source_context": record.model_dump(mode="json"),
            }}))
            audit.append({"context_id": context_id, "url": str(doc.url), "http_status": response.status_code,
                          "final_url": response.url, "original_source": sources[source_id].model_dump(mode="json"),
                          "original_document_id": doc.id, "original_chunk_id": doc.metadata["chunk_id"],
                          "original_claim": claim, "source_paragraph_index": i,
                          "extraction": "BeautifulSoup DOM get_text(' ', strip=True); structural paragraph separators only",
                          "label_or_review_change": False, "authorship": "source capture, not a new human Gold approval"})
        inputs[replay.case_id] = replay.model_copy(update={"documents": documents})
    assert len(records) == 2
    OUTPUT.mkdir()
    REPORT.mkdir()
    for directory in (OUTPUT, REPORT):
        (directory / ".gitattributes").write_bytes(b"*.json text eol=lf\n*.jsonl text eol=lf\n*.md text eol=lf\n.gitattributes text eol=lf\n")
    write_json(REPORT / "protected_artifacts.json", {
        "schema_version": "influence-context-protection-v1", "file_count": len(protected), "files": protected,
        "runtime_before": runtime_before, "suite_manifest_sha256": file_hash(BASE / "manifest.json"),
        "code_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    })
    (OUTPUT / "inputs.jsonl").write_bytes("".join(json.dumps(r.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n" for r in inputs.values()).encode())
    (OUTPUT / "context_sources.jsonl").write_bytes("".join(json.dumps(r.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n" for r in records).encode())
    write_json(OUTPUT / "schema.json", RelationSourceContext.model_json_schema())
    write_json(OUTPUT / "capture_audit.json", audit)
    write_json(OUTPUT / "manifest.json", {
        "kind": "relation_context_input", "version": "semantic-relations-context-inputs-v1",
        "created_at": datetime.now(UTC).isoformat(), "base_directory": "../semantic_relations_v1_review_r6",
        "base_manifest_sha256": file_hash(BASE / "manifest.json"), "base_files": manifest["files"],
        "base_review_files": manifest["review_files"], "label_changes": 0, "claim_changes": 0,
        "context_capture_count": len(records), "input_count": len(inputs),
        "files": {p.name: file_hash(p) for p in sorted(OUTPUT.iterdir()) if p.is_file()},
    })
    load_suite(OUTPUT)
    print(json.dumps({"context_captures": len(records), "protected_files": len(protected), "inputs": len(inputs)}))


if __name__ == "__main__":
    build()
