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

    def _error(self, status, error_code, error_message, raw_response=None):
        return {
            "status": status,
            "provider_status": "error",
            "error_source": "aml-shkeeper",
            "error_code": error_code,
            "error_message": error_message,
            "raw_response": raw_response,
        }

    def _request(self, method, path, **kwargs):
        url = f"{self.host}{path}"
        request = requests.post if method == "post" else requests.get
        try:
            response = request(url, auth=self.auth, timeout=self.timeout, **kwargs)
            try:
                data = response.json()
            except ValueError:
                data = self._error(
                    "error",
                    "invalid_json",
                    response.text,
                    raw_response=response.text,
                )
            if not isinstance(data, dict):
                data = self._error(
                    "error",
                    "invalid_json_shape",
                    "aml-shkeeper returned non-object JSON",
                    raw_response=data,
                )
            if response.status_code >= 400:
                data.setdefault("provider_status", "error")
                data.setdefault("error_source", "aml-shkeeper")
                data.setdefault("error_code", f"http_{response.status_code}")
                data.setdefault("error_message", response.text)
            return data
        except requests.RequestException as exc:
            return self._error("transport_error", "transport_error", str(exc))
