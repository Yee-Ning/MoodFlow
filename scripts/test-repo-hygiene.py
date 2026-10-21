"""Check the actual Git index and ignore rules before publishing the project."""

from pathlib import Path
import hashlib
import json
import re
import subprocess
import unittest
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]


def git(*args, input=None):
    return subprocess.run(
        ["git", *args], cwd=ROOT, input=input, capture_output=True, check=True
    ).stdout


class RepositoryHygieneTests(unittest.TestCase):
    def test_model_checksums_match_versioned_bytes_after_git_filters(self):
        directory = "ai-service/artifacts/intent_model/"
        manifest = json.loads((ROOT / directory / "manifest.json").read_text(encoding="utf-8"))
        for relative, expected in manifest["files"].items():
            if relative.endswith(".safetensors"):
                continue  # LFS content is independently checked by setup.py.
            with self.subTest(path=relative):
                versioned = git("show", ":" + directory + relative)
                self.assertEqual(hashlib.sha256(versioned).hexdigest(), expected)

    def test_index_excludes_local_reports_and_sensitive_files(self):
        forbidden = (
            "workspace/", "visual-qa/", "docs/reports/", "docs/plans/",
            "docs/superpowers/", "docs/project-summary/",
            "docs/deployment-preparation/", "ai-service/evals/", "ai-service/tools/",
            "ai-service/data/source_ingestion/worldline-native-positive",
            "ai-service/data/knowledge/worldline-native-positive",
        )
        for path in git("ls-files", "-z").decode().split("\0"):
            if not path:
                continue
            with self.subTest(path=path):
                self.assertFalse(path.startswith(forbidden))
                self.assertFalse(path.startswith("ai-service/data/raw/html/") and path.endswith(".html"))
                parts = Path(path).parts
                self.assertFalse(set(parts) & {
                    "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache",
                    ".deepeval", ".venv", "target", "dist",
                })
                name = Path(path).name
                self.assertFalse(
                    (name == ".env" or name.startswith(".env."))
                    and not name.endswith(".example")
                )
                self.assertFalse(name.endswith((".pem", ".key", ".p12", ".pfx")))
                self.assertFalse(name.endswith(("_report.md", "-report.md")))
                self.assertFalse(name.startswith("design-qa") and name.endswith(".md"))
                self.assertFalse(name.startswith("validation7"))
                self.assertNotEqual(name, "test_validation7_guards.py")
                self.assertNotIn(name, {"combo_robustness_live.py", "tools_t1t2_live.py"})
                if path.startswith("ai-service/data/knowledge/"):
                    self.assertNotIn(name, {"native_preparation_run.json", "provider_trace.jsonl"})
                if path.startswith("ai-service/data/indexing/"):
                    self.assertNotEqual(name, "native_retrieval_smoke.json")

    def test_ignore_rules_cover_outputs_but_keep_source_and_examples(self):
        ignored = [
            "workspace/new-review/result.json", "visual-qa/screen.png",
            "docs/reports/new/result.md", "ai-service/evals/reports/result.json",
            "ai-service/evals/old_benchmark.py", "ai-service/tools/old_repair.py",
            "new_report.md", "ai-service/.env", "ai-service/.env.production",
            "soul-music-room/node_modules/example/index.js", ".pytest_cache/state",
            "soul-music-room/design-qa.md", "docs/component_report.md",
            "docs/component-report.md", "soul-music-room/scripts/validation7.html",
            "ai-service/scripts/validation7_runtime.py",
            "ai-service/scripts/combo_robustness_live.py",
            "ai-service/scripts/tools_t1t2_live.py",
            "ai-service/tests/test_validation7_guards.py",
            "ai-service/data/source_ingestion/worldline-native-positive-v4/result.json",
            "ai-service/data/knowledge/worldline-native-positive-v4/provider_trace.jsonl",
            "ai-service/data/knowledge/current/provider_trace.jsonl",
            "ai-service/data/indexing/current/native_retrieval_smoke.json",
        ]
        kept = [
            ".env.example", "ai-service/.env.example",
            "ai-service/.env.worldline-online.example", "README.md",
            "ai-service/app/agent/decision/jev/config/router_capability_contract.json",
            "ai-service/tests/test_example.py",
            "soul-music-room/THIRD_PARTY_NOTICES.md",
            "ai-service/data/knowledge/releases/current/knowledge_quality_report.json",
            "ai-service/scripts/handoff_acceptance.py",
            "docs/contracts/context-pack.md",
        ]
        output = git(
            "check-ignore", "--no-index", "--stdin", input=("\n".join(ignored + kept) + "\n").encode()
        ).decode().splitlines()
        self.assertEqual(set(output), set(ignored))

    def test_project_documentation_has_no_broken_local_links_or_machine_paths(self):
        paths = git("ls-files", "-z", "*.md").decode().split("\0")
        for path in paths:
            if not path or path.startswith(("ai-service/data/", "ai-service/tests/")):
                continue  # Corpus documents and parser fixtures are source data.
            source = (ROOT / path).read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertNotRegex(source, r"[A-Za-z]:[/\\](?:Users|code|ProjectsAll)[/\\]")
                source = re.sub(r"```.*?```", "", source, flags=re.DOTALL)
                for target in re.findall(r"\]\(([^\s)]+)\)", source):
                    link = urlsplit(target.strip("<>"))
                    if link.scheme or link.netloc or not link.path:
                        continue
                    resolved = (ROOT / path).parent / unquote(link.path)
                    self.assertTrue(resolved.exists(), f"Broken link in {path}: {target}")

    def test_knowledge_release_keeps_all_signed_artifacts(self):
        paths = git("ls-files", "-z", "*knowledge_release_manifest.json").decode().split("\0")
        manifests = [path for path in paths if path]
        self.assertTrue(manifests, "Local initialization needs an approved knowledge release")
        for path in manifests:
            manifest = json.loads((ROOT / path).read_text(encoding="utf-8"))
            directory = Path(path).parent
            for relative, expected in manifest["artifacts"].items():
                with self.subTest(manifest=path, artifact=relative):
                    artifact = directory / relative
                    versioned = git("show", ":" + artifact.as_posix())
                    self.assertEqual("sha256:" + hashlib.sha256(versioned).hexdigest(), expected)

    def test_required_modules_and_submodule_are_present(self):
        paths = set(git("ls-files", "-z").decode().split("\0"))
        self.assertTrue({
            "README.md", "DEVELOPMENT.md", "soul-music-room/package.json",
            "soul-music-room/package-lock.json", "music-backend/pom.xml",
            "ai-service/requirements.txt", "ai-service/app/main.py",
            "netease-music-api", "compose.yaml", "LICENSE",
        } <= paths)
        modules = git("config", "-f", ".gitmodules", "--get-regexp", r"submodule\..*\.path").decode().splitlines()
        self.assertEqual(modules, ["submodule.netease-music-api.path netease-music-api"])
        self.assertEqual(git("config", "-f", ".gitmodules", "--get", "submodule.netease-music-api.url").decode().strip(),
                         "https://github.com/Yee-Ning/MoodFlow.git")
        self.assertTrue(git("ls-files", "--stage", "netease-music-api").startswith(b"160000 "))

    def test_core_regression_samples_are_tracked_without_report_dependencies(self):
        paths = set(git("ls-files", "-z").decode().split("\0"))
        for name in ("legacy_call_manifest.json", "explorer_catalog_worldline.json"):
            self.assertIn("ai-service/tests/fixtures/" + name, paths)
        for name in (
            "test_context_source_manifest_compat.py", "test_agent_pack_impl_a.py",
            "test_explorer_journey_v1.py",
        ):
            source = (ROOT / "ai-service/tests" / name).read_text(encoding="utf-8")
            self.assertNotIn("docs/reports/", source)


if __name__ == "__main__":
    unittest.main()
