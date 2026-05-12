import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


from generate_release_body import Commit
from prepare_release_notes import write_release_notes


class PrepareReleaseNotesTests(unittest.TestCase):
    def test_write_release_notes_creates_tag_specific_manual_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, written = write_release_notes(
                "v1.2.4",
                root=Path(tmp),
                previous_tag="v1.2.3",
                commits=[Commit("a1", "feat(updater): show update notes", "Alice")],
                repo_url="https://github.com/myuan19/voiceInput",
            )

            self.assertTrue(written)
            self.assertEqual(path, Path(tmp) / ".github" / "release-notes" / "v1.2.4.md")
            body = path.read_text(encoding="utf-8")
            self.assertFalse(body.startswith("## VoiceInput v1.2.4"))
            self.assertIn("show update notes", body)

    def test_write_release_notes_does_not_overwrite_existing_file_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".github" / "release-notes" / "v1.2.4.md"
            path.parent.mkdir(parents=True)
            path.write_text("manual body", encoding="utf-8")

            result_path, written = write_release_notes(
                "v1.2.4",
                root=root,
                previous_tag="v1.2.3",
                commits=[Commit("a1", "feat(updater): show update notes", "Alice")],
                repo_url="https://github.com/myuan19/voiceInput",
            )

            self.assertFalse(written)
            self.assertEqual(result_path, path)
            self.assertEqual(path.read_text(encoding="utf-8"), "manual body")


if __name__ == "__main__":
    unittest.main()
