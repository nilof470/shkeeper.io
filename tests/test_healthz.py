import os
import unittest

from shkeeper import create_app, db, scheduler
from shkeeper.modules.classes.crypto import Crypto


class HealthzTestCase(unittest.TestCase):
    def setUp(self):
        self.original_env = {
            name: os.environ.get(name)
            for name in ("BTC_WALLET", "LTC_WALLET", "DOGE_WALLET")
        }
        for name in self.original_env:
            os.environ[name] = "disabled"

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

    def test_healthz_returns_ok_without_authentication(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
