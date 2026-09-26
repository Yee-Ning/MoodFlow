"""Verify review-preparation code freezes without prohibiting later repairs."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import subprocess


def assert_historical_runtime_scope(path: Path, scope: dict) -> None:
    repo = Path(__file__).resolve().parents[3]
    relative = path.resolve().relative_to(repo).as_posix()
    revision = subprocess.check_output(
        ["git", "log", "--diff-filter=A", "--format=%H", "-1", "--", relative],
        cwd=repo, text=True,
    ).strip()
    assert revision, "review freeze creation commit missing"
    original = subprocess.check_output(["git", "show", f"{revision}:{relative}"], cwd=repo)
    assert json.loads(original) == scope, "historical scope lock changed"
    hashes = scope["runtime_and_evaluation_file_sha256"]
    stream = BytesIO(subprocess.check_output(
        ["git", "cat-file", "--batch"], cwd=repo,
        input="".join(f"{revision}:ai-service/{name}\n" for name in hashes).encode(),
    ))
    for name, digest in hashes.items():
        header = stream.readline().split()
        assert len(header) == 3 and header[1] == b"blob", name
        blob = stream.read(int(header[2]))
        assert stream.read(1) == b"\n"
        assert sha256(blob).hexdigest() == digest, name
