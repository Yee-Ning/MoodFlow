"""Report-53: isolated v2 batch; reuse the unchanged report-52 engine."""

import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import sys
from unittest.mock import patch

from tools.v52_offline_runtime import dimension_034 as batch


BASE_PROMPT = batch.single.EXPANSION_PROMPT
APPEND = "上述拆分策略必须服从前文约束。维度使用中性信息类别，不得写入原题未给出的具体取值、候选答案或未经确认的关联；例如可问乐器使用、曲风融合、作品例证，不可自行填入具体流派、乐器、作品或人物。三条查询的集合必须覆盖原题全部对象、带《》的作品名及全部子问题；每条须独立保留其所问内容涉及的来源、年代/统计窗口、排除和数量限制。对原子事实题允许保留同一精确信息目标的近义检索，不强拆成答案假设。最终输出前内部自检，仍只返回既定queries JSON。"
EXPANSION_PROMPT = BASE_PROMPT + "\n\n" + APPEND
V1_OUTPUT = batch.OUTPUT
OUTPUT = V1_OUTPUT.parent / "h1-dimension-prompt-034-v2-20261019"
BASE_PROTECTED_HASHES = batch.protected_hashes
AUTHORIZATION = {
    **batch.AUTHORIZATION,
    "scope": "West-approved report-53 independent v2 batch; all34 one physical generation each, then68 paired retrieval only if all pass",
    "prompt_append_text": APPEND,
    "prompt_append_sha256": hashlib.sha256(APPEND.encode()).hexdigest(),
    "prompt_sha256": hashlib.sha256(EXPANSION_PROMPT.encode()).hexdigest(),
    "generation_retries": 0,
    "v1_accepted_queries_reused": 0,
    "analysis_protocol_source": "workspace/moodflow-review/h1-dimension-prompt-034-20261019/analysis-protocol.json",
}


def v1_hashes():
    manifest = V1_OUTPUT / "artifact-sha256.json"
    pins = batch.base.read_json(manifest)["files"]
    for name, digest in pins.items():
        if batch.base.sha256(batch.base.ROOT / name) != digest:
            raise batch.base.BaselineError("v2_v1_artifact_drift:" + name)
    expected = {name for name in pins if name.startswith(V1_OUTPUT.relative_to(batch.base.ROOT).as_posix() + "/")}
    expected.add(manifest.relative_to(batch.base.ROOT).as_posix())
    actual = {p.relative_to(batch.base.ROOT).as_posix() for p in V1_OUTPUT.rglob("*") if p.is_file()}
    if actual != expected:
        raise batch.base.BaselineError("v2_v1_file_population_drift")
    return {**pins, manifest.relative_to(batch.base.ROOT).as_posix(): batch.base.sha256(manifest)}


def protected_hashes():
    return {**BASE_PROTECTED_HASHES(), **v1_hashes()}


def assert_prompt():
    report = (V1_OUTPUT / "report-52.md").read_text(encoding="utf-8")
    if report.count("> " + APPEND + "\n") != 1:
        raise batch.base.BaselineError("v2_append_differs_from_approved_report52")


@contextmanager
def configured():
    assert_prompt()
    v1_hashes()
    with patch.object(batch, "OUTPUT", OUTPUT), patch.object(batch, "AUTHORIZATION", AUTHORIZATION), \
         patch.object(batch, "protected_hashes", protected_hashes), \
         patch.object(batch.single, "EXPANSION_PROMPT", EXPANSION_PROMPT):
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "run"))
    args = parser.parse_args()
    batch.base.h2.configure()
    with configured():
        if args.command == "generate":
            sys.addaudithook(batch.h1.secret_guard)
            return asyncio.run(batch.generate())
        import torch

        print({"cuda_bootstrap": torch.cuda.is_available()}, flush=True)
        guard = batch.base.h2.ReadOnlyGuard()
        sys.addaudithook(guard.audit)
        with batch.single.anonymous_local_models():
            return batch.run(guard)


if __name__ == "__main__":
    raise SystemExit(main())
