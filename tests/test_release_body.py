import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


from generate_release_body import Commit, load_manual_release_body, render_release_body


class ReleaseBodyTests(unittest.TestCase):
    def test_render_release_body_groups_mainstream_sections(self):
        body = render_release_body(
            version="1.2.4",
            previous_tag="v1.2.3",
            current_tag="v1.2.4",
            commits=[
                Commit("a1", "feat(update): show release notes before updating", "Alice"),
                Commit("b2", "fix(startup): handle missing api key", "Bob"),
                Commit("c3", "perf(audio): reduce recorder startup delay", "Alice"),
                Commit("d4", "refactor(tray): simplify update state handling", "Dora"),
                Commit("d4", "docs: update quick start", "Carol"),
                Commit("e5", "chore: release v1.2.4", "Bot"),
            ],
            repo_url="https://github.com/myuan19/voiceInput",
        )

        self.assertFalse(body.startswith("## VoiceInput v1.2.4"))
        self.assertIn("### 新增", body)
        self.assertIn("show release notes before updating", body)
        self.assertIn("### 修复", body)
        self.assertIn("handle missing api key", body)
        self.assertIn("### 性能优化", body)
        self.assertIn("reduce recorder startup delay", body)
        self.assertIn("### 重构", body)
        self.assertIn("simplify update state handling", body)
        self.assertNotIn("### 文档", body)
        self.assertNotIn("update quick start", body)
        self.assertNotIn("release v1.2.4", body)
        self.assertIn("https://github.com/myuan19/voiceInput/compare/v1.2.3...v1.2.4", body)
        self.assertIn("@Alice", body)
        self.assertIn("@Bob", body)
        self.assertIn("@Dora", body)
        self.assertNotIn("@Carol", body)

    def test_render_release_body_omits_uncategorized_commits(self):
        body = render_release_body(
            version="1.2.4",
            previous_tag="",
            current_tag="v1.2.4",
            commits=[Commit("a1", "improve tray update copy", "")],
            repo_url="https://github.com/myuan19/voiceInput",
        )

        self.assertNotIn("### 其他变更", body)
        self.assertNotIn("improve tray update copy", body)
        self.assertNotIn("/compare/", body)

    def test_load_manual_release_body_uses_tag_specific_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manual_dir = root / ".github" / "release-notes"
            manual_dir.mkdir(parents=True)
            manual = manual_dir / "v1.2.4.md"
            manual.write_text("## 手动更新说明\n\n- 修复启动问题\n", encoding="utf-8")

            self.assertEqual(
                load_manual_release_body("v1.2.4", root=root),
                "## 手动更新说明\n\n- 修复启动问题\n",
            )


if __name__ == "__main__":
    unittest.main()
