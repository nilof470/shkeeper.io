import os
import sys
import tempfile
import unittest
from unittest import mock

import sqlalchemy as sa

from shkeeper import create_app, db, scheduler, _payout_cli_disables_scheduler
from shkeeper.modules.classes.crypto import Crypto
from shkeeper.models import (
    PayoutAuthNonce,
    PayoutCallbackEvent,
    PayoutExecution,
    PayoutExecutionResolutionAudit,
    PayoutRail,
    User,
)


class HealthzTestCase(unittest.TestCase):
    def setUp(self):
        self.original_env = {
            name: os.environ.get(name)
            for name in (
                "BTC_WALLET",
                "LTC_WALLET",
                "DOGE_WALLET",
            )
        }
        for name in self.original_env:
            os.environ[name] = "disabled"
        self.original_disable_scheduler = os.environ.pop("DISABLE_SCHEDULER", None)
        self.original_aml_max_accept_score = os.environ.pop(
            "AML_MAX_ACCEPT_SCORE", None
        )

        self.original_crypto_instances = dict(Crypto.instances)
        Crypto.instances.clear()

        self.app = create_app(
            {
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "SESSION_TYPE": "filesystem",
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        if scheduler.running:
            scheduler.shutdown(wait=False)

        with self.app.app_context():
            db.session.remove()
            db.drop_all()

        Crypto.instances.clear()
        Crypto.instances.update(self.original_crypto_instances)

        for name, value in self.original_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if self.original_disable_scheduler is None:
            os.environ.pop("DISABLE_SCHEDULER", None)
        else:
            os.environ["DISABLE_SCHEDULER"] = self.original_disable_scheduler
        if self.original_aml_max_accept_score is None:
            os.environ.pop("AML_MAX_ACCEPT_SCORE", None)
        else:
            os.environ["AML_MAX_ACCEPT_SCORE"] = self.original_aml_max_accept_score

    def test_healthz_returns_ok_without_authentication(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_default_aml_max_accept_score_is_zero_point_thirty(self):
        self.assertEqual(self.app.config["AML_MAX_ACCEPT_SCORE"], "0.30")

    def test_payout_execution_reconciler_cli_command_is_registered(self):
        self.assertIn("payout-execution-reconciler", self.app.cli.commands)
        self.assertIn("payout-callback-dispatcher", self.app.cli.commands)

    def test_disable_scheduler_env_is_loaded(self):
        if scheduler.running:
            scheduler.shutdown(wait=False)
        os.environ["DISABLE_SCHEDULER"] = "true"

        app = create_app(
            {
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "SESSION_TYPE": "filesystem",
            }
        )

        self.assertTrue(app.config["DISABLE_SCHEDULER"])
        self.assertFalse(scheduler.running)

    def test_payout_cli_commands_disable_scheduler_by_default(self):
        for command in (
            "payout-execution-reconciler",
            "payout-callback-dispatcher",
            "payout-rail-sync",
        ):
            with self.subTest(command=command):
                with mock.patch.object(sys, "argv", ["flask", command]):
                    self.assertTrue(_payout_cli_disables_scheduler())
                    if scheduler.running:
                        scheduler.shutdown(wait=False)
                    app = create_app(
                        {
                            "TESTING": True,
                            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                            "SESSION_TYPE": "filesystem",
                        }
                    )
                    self.assertTrue(app.config["DISABLE_SCHEDULER"])
                    self.assertFalse(scheduler.running)

    def test_payout_cli_commands_disable_scheduler_even_when_env_is_false(self):
        os.environ["DISABLE_SCHEDULER"] = "false"
        for command in (
            "payout-execution-reconciler",
            "payout-callback-dispatcher",
            "payout-rail-sync",
        ):
            with self.subTest(command=command):
                with mock.patch.object(sys, "argv", ["flask", command]):
                    if scheduler.running:
                        scheduler.shutdown(wait=False)
                    app = create_app(
                        {
                            "TESTING": True,
                            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                            "SESSION_TYPE": "filesystem",
                        }
                    )
                    self.assertTrue(app.config["DISABLE_SCHEDULER"])
                    self.assertFalse(scheduler.running)

    def test_existing_database_runs_migrations_before_create_all(self):
        if scheduler.running:
            scheduler.shutdown(wait=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "legacy.sqlite")
            uri = f"sqlite:///{db_path}"
            legacy_app = create_app(
                {
                    "TESTING": True,
                    "SQLALCHEMY_DATABASE_URI": uri,
                    "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                    "SESSION_TYPE": "filesystem",
                }
            )
            if scheduler.running:
                scheduler.shutdown(wait=False)
            with legacy_app.app_context():
                for model in (
                    PayoutAuthNonce,
                    PayoutCallbackEvent,
                    PayoutExecutionResolutionAudit,
                    PayoutExecution,
                    PayoutRail,
                ):
                    model.__table__.drop(db.engine, checkfirst=True)
                db.session.execute(sa.text("DELETE FROM alembic_version"))
                db.session.execute(
                    sa.text(
                        "INSERT INTO alembic_version (version_num) "
                        "VALUES ('20260529_payout_external_id_unique')"
                    )
                )
                if not User.query.filter_by(username="admin").first():
                    db.session.add(User(username="admin"))
                db.session.commit()

            upgraded_app = create_app(
                {
                    "TESTING": True,
                    "SQLALCHEMY_DATABASE_URI": uri,
                    "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                    "SESSION_TYPE": "filesystem",
                }
            )
            if scheduler.running:
                scheduler.shutdown(wait=False)
            with upgraded_app.app_context():
                table_names = set(sa.inspect(db.engine).get_table_names())
                self.assertIn("payout_rail", table_names)
                self.assertIn("payout_execution", table_names)


if __name__ == "__main__":
    unittest.main()
