import importlib.util
import py_compile
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_DIR = ROOT / "deploy" / "shkeeper"


def load_script_module(name):
    path = DEPLOY_DIR / name
    module_name = name.replace("-", "_").replace(".py", "")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(DEPLOY_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(DEPLOY_DIR))
    return module


class ShkeeperDeployScriptsTestCase(unittest.TestCase):
    def test_scripts_exist(self):
        for name in (
            "verify-tron-usdt-payout-worker.py",
            "upgrade.sh",
            "README.md",
        ):
            with self.subTest(name=name):
                self.assertTrue((DEPLOY_DIR / name).exists())

    def test_python_scripts_compile(self):
        py_compile.compile(
            str(DEPLOY_DIR / "verify-tron-usdt-payout-worker.py"),
            doraise=True,
        )

    def test_upgrade_script_has_valid_shell_syntax(self):
        subprocess.run(["sh", "-n", str(DEPLOY_DIR / "upgrade.sh")], check=True)

    def test_upgrade_help_documents_worker_invariant(self):
        result = subprocess.run(
            ["sh", str(DEPLOY_DIR / "upgrade.sh"), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("production deploy entry point", result.stdout)
        self.assertIn("TRON USDT payout worker", result.stdout)
        self.assertIn("chart fork", result.stdout)
        self.assertNotIn("post-renderer", result.stdout)

    def test_upgrade_script_uses_chart_fork_not_post_renderer(self):
        script = (DEPLOY_DIR / "upgrade.sh").read_text()

        self.assertIn("oci://ghcr.io/nilof470/helm-charts/shkeeper", script)
        self.assertIn("CHART_VERSION", script)
        self.assertNotIn("--post-renderer", script)
        self.assertNotIn("python3 -c 'import yaml'", script)

    def test_removed_post_renderer_scripts_are_not_part_of_deploy_path(self):
        for name in (
            "apply-tron-usdt-payout-worker.py",
            "payout_worker_manifest.py",
            "tron-usdt-payout-worker-post-renderer.py",
        ):
            with self.subTest(name=name):
                self.assertFalse((DEPLOY_DIR / name).exists())

    def test_verify_helpers_check_exact_celery_options(self):
        module = load_script_module("verify-tron-usdt-payout-worker.py")

        self.assertTrue(
            module.command_option_equals(
                ["worker", "--concurrency=1"],
                "--concurrency",
                "1",
            )
        )
        self.assertTrue(
            module.command_option_equals(
                ["worker", "--prefetch-multiplier", "1"],
                "--prefetch-multiplier",
                "1",
            )
        )
        self.assertFalse(
            module.command_option_equals(
                ["worker", "--concurrency", "--prefetch-multiplier", "1"],
                "--concurrency",
                "1",
            )
        )
        self.assertEqual(
            module.expected_payout_queue(
                {
                    "env": [
                        {
                            "name": "TRON_USDT_PAYOUT_QUEUE",
                            "value": "custom_usdt_payouts",
                        },
                    ],
                }
            ),
            "custom_usdt_payouts",
        )
        self.assertFalse(
            module.pod_container_count_matches(
                {"status": {"containerStatuses": [{"ready": True}]}},
                2,
            )
        )


if __name__ == "__main__":
    unittest.main()
