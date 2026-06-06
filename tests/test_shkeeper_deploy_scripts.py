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
        self.assertIn("TRON payout topology", result.stdout)
        self.assertIn("chart fork", result.stdout)
        self.assertNotIn("post-renderer", result.stdout)

    def test_upgrade_script_uses_chart_fork_not_post_renderer(self):
        script = (DEPLOY_DIR / "upgrade.sh").read_text()

        self.assertIn("oci://ghcr.io/nilof470/helm-charts/shkeeper", script)
        self.assertIn('DEFAULT_CHART_VERSION="1.7.28-nilof470.10"', script)
        self.assertIn("deployment/tron-usdt-payouts", script)
        self.assertIn("CHART_VERSION", script)
        self.assertNotIn("--atomic", script)
        self.assertNotIn("--wait", script)
        self.assertNotIn("--post-renderer", script)
        self.assertNotIn("python3 -c 'import yaml'", script)

    def test_deployment_docs_use_current_published_artifacts_and_values_files(self):
        deployment_doc = (ROOT / "docs" / "DEPLOYMENT.md").read_text()
        wrapper_readme = (DEPLOY_DIR / "README.md").read_text()
        combined = deployment_doc + "\n" + wrapper_readme

        self.assertIn("1.7.28-nilof470.10", combined)
        self.assertIn("ghcr.io/nilof470/shkeeper.io:92263d0", deployment_doc)
        self.assertIn(
            "sha256:d0da1a8763f72c1e8f66a1755bc985d2c8414ac124c8335bfc71813fd29fc92e",
            deployment_doc,
        )
        self.assertNotIn("1.7.28-nilof470.8", combined)
        self.assertNotIn("ghcr.io/nilof470/shkeeper.io:aa8cb3e", deployment_doc)
        self.assertNotIn("sha256:886d87b990c2756f0e9da3a63f54941bea28ae31fb4be2556558ad743854a5ea", deployment_doc)
        self.assertNotIn("/root/shkeeper-current-values.yaml", deployment_doc)
        self.assertNotIn("payout-secret-guard.py", combined)

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

    def test_verify_helpers_accept_separate_tron_payout_worker_deployment(self):
        module = load_script_module("verify-tron-usdt-payout-worker.py")
        api_deployment = {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {"name": "app", "env": []},
                            {
                                "name": "tasks",
                                "command": [
                                    "celery",
                                    "-A",
                                    "celery_worker.celery",
                                    "worker",
                                    "-Q",
                                    "celery",
                                ],
                                "env": [
                                    {
                                        "name": "TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED",
                                        "value": "true",
                                    },
                                    {
                                        "name": "TRON_USDT_PAYOUT_QUEUE",
                                        "value": "tron_usdt_fee_payouts",
                                    },
                                ],
                            },
                            {"name": "redis", "env": []},
                        ]
                    }
                }
            }
        }
        worker_deployment = {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "tron-usdt-payouts",
                                "command": [
                                    "celery",
                                    "-A",
                                    "celery_worker.celery",
                                    "worker",
                                    "-Q",
                                    "tron_usdt_fee_payouts",
                                    "--concurrency=1",
                                    "--prefetch-multiplier=1",
                                ],
                                "env": [
                                    {
                                        "name": "REDIS_HOST",
                                        "value": "tron-shkeeper:6379",
                                    },
                                    {
                                        "name": "TRON_USDT_PAYOUT_QUEUE",
                                        "value": "tron_usdt_fee_payouts",
                                    },
                                ],
                            }
                        ]
                    }
                }
            }
        }

        api_containers, queue, feature_enabled = module.verify_api_deployment(
            api_deployment
        )
        worker_containers = module.verify_worker_deployment(worker_deployment, queue)

        self.assertTrue(feature_enabled)
        self.assertEqual(queue, "tron_usdt_fee_payouts")
        self.assertEqual(set(api_containers), {"app", "tasks", "redis"})
        self.assertEqual(set(worker_containers), {"tron-usdt-payouts"})


if __name__ == "__main__":
    unittest.main()
