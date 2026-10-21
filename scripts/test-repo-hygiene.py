"""Check the actual Git index and ignore rules before publishing the project."""

from pathlib import Path
import hashlib
import json
import subprocess
import unittest


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
                if "/" not in path:
                    self.assertFalse(name.endswith("_report.md"))

    def test_ignore_rules_cover_outputs_but_keep_source_and_examples(self):
        ignored = [
            "workspace/new-review/result.json", "visual-qa/screen.png",
            "docs/reports/new/result.md", "ai-service/evals/reports/result.json",
            "ai-service/evals/old_benchmark.py", "ai-service/tools/old_repair.py",
            "new_report.md", "ai-service/.env", "ai-service/.env.production",
            "soul-music-room/node_modules/example/index.js", ".pytest_cache/state",
        ]
        kept = [
            ".env.example", "ai-service/.env.example",
            "ai-service/.env.worldline-online.example", "README.md",
            "ai-service/app/agent/decision/jev/config/router_capability_contract.json",
            "ai-service/tests/test_example.py",
        ]
        output = git(
            "check-ignore", "--no-index", "--stdin", input=("\n".join(ignored + kept) + "\n").encode()
        ).decode().splitlines()
        self.assertEqual(set(output), set(ignored))

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
