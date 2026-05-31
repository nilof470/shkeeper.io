import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "contrib" / "vps-memory-triage.sh"


class VpsMemoryTriageScriptTestCase(unittest.TestCase):
    def test_script_exists(self):
        self.assertTrue(SCRIPT.exists(), "contrib/vps-memory-triage.sh is missing")

    def test_script_has_valid_shell_syntax(self):
        subprocess.run(["sh", "-n", str(SCRIPT)], check=True)

    def test_help_documents_safe_mode(self):
        result = subprocess.run(
            ["sh", str(SCRIPT), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("dry-run", result.stdout)
        self.assertIn("--apply", result.stdout)
        self.assertIn("--stabilize-small-vps", result.stdout)


if __name__ == "__main__":
    unittest.main()
