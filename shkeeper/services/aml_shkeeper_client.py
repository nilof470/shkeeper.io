import requests
from flask import current_app


class AmlShkeeperClient:
    def __init__(self, app_config=None):
        self.config = app_config or current_app.config
        self.host = self.config["AML_SHKEEPER_HOST"].rstrip("/")
        self.auth = (
            self.config["AML_SHKEEPER_USERNAME"],
            self.config["AML_SHKEEPER_PASSWORD"],
        )
        self.timeout = self.config.get("REQUESTS_TIMEOUT", 10)

    def create_check(self, payload):
        return self._request("post", "/api/v1/checks", json=payload)

    def get_check(self, deposit_id):
        return self._request("get", f"/api/v1/checks/{deposit_id}")

    def _request(self, method, path, **kwargs):
        url = f"{self.host}{path}"
        request = requests.post if method == "post" else requests.get
        try:
            response = request(url, auth=self.auth, timeout=self.timeout, **kwargs)
            try:
                data = response.json()
            except ValueError:
                data = {
                    "status": "error",
                    "provider": "amlbot",
                    "provider_status": "error",
                    "error_code": "invalid_json",
                    "error_message": response.text,
                }
            if response.status_code >= 400:
                data.setdefault("provider", "amlbot")
                data.setdefault("provider_status", "error")
                data.setdefault("error_code", f"http_{response.status_code}")
                data.setdefault("error_message", response.text)
            return data
        except requests.RequestException as exc:
            return {
                "status": "transport_error",
                "provider": "amlbot",
                "provider_status": "error",
                "error_code": "transport_error",
                "error_message": str(exc),
            }
