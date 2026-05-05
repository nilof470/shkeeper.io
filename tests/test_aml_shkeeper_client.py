import unittest

from shkeeper.services.aml_shkeeper_client import AmlShkeeperClient


class Response:
    def __init__(self, status_code, payload, text="response body"):
        self.status_code = status_code
        self.payload = payload
        self.text = text

    def json(self):
        return self.payload


class AmlShkeeperClientTestCase(unittest.TestCase):
    def test_http_error_with_non_object_json_is_normalized(self):
        import shkeeper.services.aml_shkeeper_client as client_module

        original_post = client_module.requests.post
        client_module.requests.post = lambda *args, **kwargs: Response(
            500, ["bad"], text="[\"bad\"]"
        )
        try:
            client = AmlShkeeperClient(
                {
                    "AML_SHKEEPER_HOST": "http://aml-shkeeper",
                    "AML_SHKEEPER_USERNAME": "shkeeper",
                    "AML_SHKEEPER_PASSWORD": "shkeeper",
                    "REQUESTS_TIMEOUT": 1,
                }
            )
            result = client.create_check({"deposit_id": "deposit-1"})
        finally:
            client_module.requests.post = original_post

        self.assertEqual(result["provider_status"], "error")
        self.assertEqual(result["error_source"], "aml-shkeeper")
        self.assertEqual(result["error_code"], "invalid_json_shape")
        self.assertNotIn("provider", result)


if __name__ == "__main__":
    unittest.main()
