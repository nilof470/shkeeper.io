import importlib.util
import json
import os
import py_compile
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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
            "payout-secret-guard.py",
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
        py_compile.compile(
            str(DEPLOY_DIR / "payout-secret-guard.py"),
            doraise=True,
        )

    def test_upgrade_script_has_valid_shell_syntax(self):
        subprocess.run(["sh", "-n", str(DEPLOY_DIR / "upgrade.sh")], check=True)

    def test_upgrade_script_payout_secret_preflight_modes(self):
        script = DEPLOY_DIR / "upgrade.sh"
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            bin_path = temp_path / "bin"
            bin_path.mkdir()
            values_file = temp_path / "values.yaml"
            values_file.write_text("test: true\n", encoding="utf-8")
            log_file = temp_path / "calls.log"

            for name, body in {
                "python3": (
                    "#!/bin/sh\n"
                    f"printf 'python3 %s\\n' \"$*\" >> {log_file}\n"
                    "exit 0\n"
                ),
                "helm": (
                    "#!/bin/sh\n"
                    f"printf 'helm %s\\n' \"$*\" >> {log_file}\n"
                    "exit 0\n"
                ),
                "kubectl": (
                    "#!/bin/sh\n"
                    f"printf 'kubectl %s\\n' \"$*\" >> {log_file}\n"
                    "case \"$*\" in\n"
                    "  *'get deployment/tron-shkeeper'*) exit 1 ;;\n"
                    "esac\n"
                    "exit 0\n"
                ),
            }.items():
                path = bin_path / name
                path.write_text(body, encoding="utf-8")
                path.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{bin_path}{os.pathsep}{env['PATH']}"

            subprocess.run(
                ["sh", str(script), str(values_file)],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            default_log = log_file.read_text(encoding="utf-8")
            self.assertIn("python3", default_log)
            self.assertIn("verify-cluster", default_log)
            self.assertIn("helm upgrade", default_log)

            log_file.write_text("", encoding="utf-8")
            skip_env = env.copy()
            skip_env["PAYOUT_SECRET_PREFLIGHT"] = "skip"
            subprocess.run(
                ["sh", str(script), str(values_file)],
                check=True,
                capture_output=True,
                text=True,
                env=skip_env,
            )
            skip_log = log_file.read_text(encoding="utf-8")
            self.assertNotIn("python3", skip_log)
            self.assertIn("helm upgrade", skip_log)

            log_file.write_text("", encoding="utf-8")
            invalid_env = env.copy()
            invalid_env["PAYOUT_SECRET_PREFLIGHT"] = "invalid"
            result = subprocess.run(
                ["sh", str(script), str(values_file)],
                capture_output=True,
                text=True,
                env=invalid_env,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("PAYOUT_SECRET_PREFLIGHT", result.stderr)
            self.assertNotIn("helm", log_file.read_text(encoding="utf-8"))

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
        self.assertIn("PAYOUT_SIDECAR_SIGNING_SECRET_KEY", result.stdout)
        self.assertIn("PAYOUT_SIDECAR_CONSUMER_SECRET_KEY", result.stdout)
        self.assertNotIn("post-renderer", result.stdout)

    def test_upgrade_script_uses_chart_fork_not_post_renderer(self):
        script = (DEPLOY_DIR / "upgrade.sh").read_text()

        self.assertIn("oci://ghcr.io/nilof470/helm-charts/shkeeper", script)
        self.assertIn('DEFAULT_CHART_VERSION="1.7.28-nilof470.9"', script)
        self.assertIn("deployment/tron-usdt-payouts", script)
        self.assertIn("payout-secret-guard.py", script)
        self.assertIn("CHART_VERSION", script)
        self.assertNotIn("--atomic", script)
        self.assertNotIn("--wait", script)
        self.assertNotIn("--post-renderer", script)
        self.assertNotIn("python3 -c 'import yaml'", script)

    def test_deployment_docs_use_current_published_artifacts_and_values_files(self):
        deployment_doc = (ROOT / "docs" / "DEPLOYMENT.md").read_text()
        wrapper_readme = (DEPLOY_DIR / "README.md").read_text()
        combined = deployment_doc + "\n" + wrapper_readme

        self.assertIn("1.7.28-nilof470.9", combined)
        self.assertIn("ghcr.io/nilof470/shkeeper.io:92263d0", deployment_doc)
        self.assertIn(
            "sha256:d0da1a8763f72c1e8f66a1755bc985d2c8414ac124c8335bfc71813fd29fc92e",
            deployment_doc,
        )
        self.assertNotIn("1.7.28-nilof470.8", combined)
        self.assertNotIn("ghcr.io/nilof470/shkeeper.io:aa8cb3e", deployment_doc)
        self.assertNotIn("sha256:886d87b990c2756f0e9da3a63f54941bea28ae31fb4be2556558ad743854a5ea", deployment_doc)
        self.assertNotIn("/root/shkeeper-current-values.yaml", deployment_doc)

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

    def test_payout_secret_guard_generates_composite_sidecar_consumer_keys(self):
        module = load_script_module("payout-secret-guard.py")
        signing_keys = {
            "grither-pay": {
                "shkeeper-to-sidecars-v1": {
                    "secret": "sidecar-secret",
                    "rails": ["TRON-USDT", "TON-USDT", "ETH-USDT"],
                }
            }
        }

        consumer_keys = module.build_sidecar_consumer_keys(signing_keys)
        summary = module.validate_sidecar_secret_contract(signing_keys, consumer_keys)

        self.assertEqual(
            consumer_keys,
            {
                "grither-pay": {
                    "rails": ["TRON-USDT", "TON-USDT", "ETH-USDT"],
                    "keys": {"shkeeper-to-sidecars-v1": "sidecar-secret"},
                    "shkeeper-to-sidecars-v1": {
                        "secret": "sidecar-secret",
                        "rails": ["TRON-USDT", "TON-USDT", "ETH-USDT"],
                    },
                }
            },
        )
        self.assertEqual(
            summary,
            {
                "consumer": "grither-pay",
                "key_ids": ["shkeeper-to-sidecars-v1"],
                "rails": ["TRON-USDT", "TON-USDT", "ETH-USDT"],
            },
        )

    def test_payout_secret_guard_accepts_tron_legacy_consumer_keys_without_top_level_keys(
        self,
    ):
        module = load_script_module("payout-secret-guard.py")
        signing_keys = {
            "grither-pay": {
                "shkeeper-to-tron-v1": {
                    "secret": "sidecar-secret",
                    "rails": ["TRON-USDT"],
                }
            }
        }
        legacy_consumer_keys = {
            "grither-pay": {
                "shkeeper-to-tron-v1": {
                    "secret": "sidecar-secret",
                    "rails": ["TRON-USDT"],
                }
            }
        }

        summary = module.validate_sidecar_secret_contract(
            signing_keys,
            legacy_consumer_keys,
            required_rails=["TRON-USDT"],
        )

        self.assertEqual(
            summary,
            {
                "consumer": "grither-pay",
                "key_ids": ["shkeeper-to-tron-v1"],
                "rails": ["TRON-USDT"],
            },
        )

    def test_payout_secret_guard_rejects_signing_json_as_sidecar_consumer_keys(self):
        module = load_script_module("payout-secret-guard.py")
        signing_keys = {
            "grither-pay": {
                "shkeeper-to-sidecars-v1": {
                    "secret": "sidecar-secret",
                    "rails": ["TRON-USDT", "TON-USDT", "ETH-USDT"],
                }
            }
        }

        with self.assertRaisesRegex(module.SecretContractError, "keys"):
            module.validate_sidecar_secret_contract(
                signing_keys,
                signing_keys,
                required_rails=["ETH-USDT"],
            )

    def test_payout_secret_guard_rejects_unknown_sidecar_key_id(self):
        module = load_script_module("payout-secret-guard.py")
        signing_keys = {
            "grither-pay": {
                "shkeeper-to-sidecars-v1": {
                    "secret": "sidecar-secret",
                    "rails": ["ETH-USDT"],
                }
            }
        }
        consumer_keys = {
            "grither-pay": {
                "rails": ["ETH-USDT"],
                "keys": {"shkeeper-to-eth-v1": "sidecar-secret"},
            }
        }

        with self.assertRaisesRegex(module.SecretContractError, "PAYOUT_AUTH_UNKNOWN_KEY"):
            module.validate_sidecar_secret_contract(
                signing_keys,
                consumer_keys,
                required_rails=["ETH-USDT"],
            )

    def test_payout_secret_guard_rejects_extra_sidecar_key_id(self):
        module = load_script_module("payout-secret-guard.py")
        signing_keys = {
            "grither-pay": {
                "shkeeper-to-sidecars-v1": {
                    "secret": "sidecar-secret",
                    "rails": ["ETH-USDT"],
                }
            }
        }
        consumer_keys = {
            "grither-pay": {
                "rails": ["ETH-USDT"],
                "keys": {
                    "shkeeper-to-sidecars-v1": "sidecar-secret",
                    "stale-key": "old-secret",
                },
            }
        }

        with self.assertRaisesRegex(module.SecretContractError, "PAYOUT_AUTH_UNKNOWN_KEY"):
            module.validate_sidecar_secret_contract(
                signing_keys,
                consumer_keys,
                required_rails=["ETH-USDT"],
            )

    def test_payout_secret_guard_rejects_extra_sidecar_rail(self):
        module = load_script_module("payout-secret-guard.py")
        signing_keys = {
            "grither-pay": {
                "shkeeper-to-sidecars-v1": {
                    "secret": "sidecar-secret",
                    "rails": ["ETH-USDT"],
                }
            }
        }
        consumer_keys = {
            "grither-pay": {
                "rails": ["ETH-USDT", "TON-USDT"],
                "keys": {"shkeeper-to-sidecars-v1": "sidecar-secret"},
            }
        }

        with self.assertRaisesRegex(module.SecretContractError, "rails"):
            module.validate_sidecar_secret_contract(
                signing_keys,
                consumer_keys,
                required_rails=["ETH-USDT"],
            )

    def test_payout_secret_guard_cli_writes_consumer_file_without_printing_secret(self):
        script = DEPLOY_DIR / "payout-secret-guard.py"
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            signing_path = temp_path / "payout-sidecar-signing-keys.json"
            consumer_path = temp_path / "payout-sidecar-consumer-keys.json"
            signing_path.write_text(
                json.dumps(
                    {
                        "grither-pay": {
                            "shkeeper-to-sidecars-v1": {
                                "secret": "sidecar-secret",
                                "rails": ["ETH-USDT"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "render-sidecar-consumer",
                    "--signing-keys-file",
                    str(signing_path),
                    "--output",
                    str(consumer_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertNotIn("sidecar-secret", result.stdout)
            self.assertNotIn("sidecar-secret", result.stderr)
            rendered = json.loads(consumer_path.read_text(encoding="utf-8"))
            self.assertEqual(
                rendered["grither-pay"]["keys"],
                {"shkeeper-to-sidecars-v1": "sidecar-secret"},
            )

    def test_payout_secret_guard_restricts_existing_output_file_before_write(self):
        module = load_script_module("payout-secret-guard.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "payout-sidecar-consumer-keys.json"
            output_path.write_text("old\n", encoding="utf-8")
            output_path.chmod(0o644)
            original_fdopen = module.os.fdopen
            original_fchmod = module.os.fchmod
            fchmod_called = False

            def record_fchmod(fd, mode):
                nonlocal fchmod_called
                fchmod_called = True
                return original_fchmod(fd, mode)

            def assert_restricted_before_write(fd, *args, **kwargs):
                self.assertTrue(fchmod_called)
                return original_fdopen(fd, *args, **kwargs)

            with mock.patch.object(module.os, "fchmod", side_effect=record_fchmod):
                with mock.patch.object(module.os, "fdopen", side_effect=assert_restricted_before_write):
                    module.write_secret_json(
                        output_path,
                        {
                            "grither-pay": {
                                "rails": ["ETH-USDT"],
                                "keys": {"shkeeper-to-sidecars-v1": "sidecar-secret"},
                            }
                        },
                    )

            self.assertEqual(output_path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
