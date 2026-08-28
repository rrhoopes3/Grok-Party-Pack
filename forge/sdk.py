"""
The Forge SDK — Python client for the agent marketplace.

Usage:
    from forge.sdk import ForgeClient

    # Register a new agent
    client = ForgeClient("http://localhost:5000")
    result = client.register("my-bot", owner="alice")
    print(result["api_key"])

    # Or connect with existing key
    client = ForgeClient("http://localhost:5000", api_key="forge_xxx")

    # Submit a task
    task = client.submit_task("list files in current directory")
    print(task["task_id"])

    # Stream results
    for event in client.stream_task(task["task_id"]):
        print(event)

    # Check balance
    print(client.get_balance())

    # Browse agents
    for agent in client.list_agents():
        print(agent["name"], agent["capabilities"])

    # Invoke another agent
    relay = client.invoke_agent("ext_other-bot", "summarize this text")
"""
from __future__ import annotations

import json
from typing import Generator

import requests


class ForgeError(Exception):
    """Base error for Forge SDK."""
    def __init__(self, message: str, status_code: int = 0, data: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.data = data or {}


class PaymentRequiredError(ForgeError):
    """Raised on HTTP 402 — agent needs to deposit funds."""
    def __init__(self, data: dict):
        super().__init__(
            f"Payment required: need ${data.get('shortfall_usd', '?')} more",
            status_code=402, data=data,
        )
        self.invoice_id = data.get("invoice_id", "")
        self.estimate_usd = data.get("estimate_usd", 0)
        self.shortfall_usd = data.get("shortfall_usd", 0)
        self.payment_methods = data.get("payment_methods", [])


class ForgeClient:
    """Python client for The Forge agent marketplace API."""

    def __init__(self, base_url: str = "http://localhost:5000", api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        headers.update(self._headers())
        kwargs.setdefault("timeout", 30)
        resp = requests.request(method, url, headers=headers, **kwargs)
        data = resp.json() if resp.content else {}

        if resp.status_code == 402:
            raise PaymentRequiredError(data)
        if resp.status_code >= 400:
            error_msg = data.get("error", data.get("message", f"HTTP {resp.status_code}"))
            raise ForgeError(error_msg, resp.status_code, data)

        return data

    # ── Registration ─────────────────────────────────────────────────────

    def register(self, name: str, owner: str = "anonymous",
                 description: str = "", capabilities: list[str] | None = None) -> dict:
        """Register a new agent. Returns {agent_id, api_key, wallet}.

        Automatically stores the API key for subsequent requests.
        """
        payload: dict = {"name": name, "owner": owner}
        if description:
            payload["description"] = description
        if capabilities:
            payload["capabilities"] = capabilities
        result = self._request("POST", "/api/v1/agents/register", json=payload)
        self.api_key = result.get("api_key", self.api_key)
        return result

    # ── Agent Info ────────────────────────────────────────────────────────

    def me(self) -> dict:
        """Get authenticated agent info."""
        return self._request("GET", "/api/v1/agents/me")

    def update_profile(self, description: str | None = None,
                       capabilities: list[str] | None = None,
                       is_public: bool | None = None) -> dict:
        """Update agent profile."""
        payload: dict = {}
        if description is not None:
            payload["description"] = description
        if capabilities is not None:
            payload["capabilities"] = capabilities
        if is_public is not None:
            payload["is_public"] = is_public
        return self._request("PATCH", "/api/v1/agents/me/profile", json=payload)

    # ── Wallet ───────────────────────────────────────────────────────────

    def get_wallet(self) -> dict:
        """Get wallet details + recent transactions."""
        return self._request("GET", "/api/v1/wallet")

    def get_balance(self) -> float:
        """Get current balance in USD."""
        data = self.get_wallet()
        return data.get("wallet", {}).get("balance_usd", 0.0)

    def deposit(self, amount_usd: float) -> dict:
        """Deposit funds into wallet."""
        return self._request("POST", "/api/v1/wallet/deposit",
                             json={"amount_usd": amount_usd})

    def check_invoice(self, invoice_id: str) -> dict:
        """Check deposit/invoice status."""
        return self._request("GET", f"/api/v1/wallet/deposit/status/{invoice_id}")

    # ── Tasks ────────────────────────────────────────────────────────────

    def submit_task(self, task: str, direct_mode: bool = True,
                    executor_model: str = "") -> dict:
        """Submit a task. Returns {task_id, stream_url, result_url}.

        Raises PaymentRequiredError if balance is insufficient.
        """
        payload: dict = {"task": task, "direct_mode": direct_mode}
        if executor_model:
            payload["executor_model"] = executor_model
        return self._request("POST", "/api/v1/tasks", json=payload)

    def stream_task(self, task_id: str) -> Generator[dict, None, None]:
        """Stream SSE events from a running task. Yields dicts."""
        url = f"{self.base_url}/api/v1/tasks/{task_id}/stream"
        resp = requests.get(url, headers=self._headers(), stream=True, timeout=(15, 120))
        if resp.status_code != 200:
            raise ForgeError(f"Stream error: HTTP {resp.status_code}", resp.status_code)

        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data = json.loads(line[6:])
            yield data
            if data.get("type") == "done":
                break

    def get_result(self, task_id: str) -> dict:
        """Get final result of a completed task."""
        return self._request("GET", f"/api/v1/tasks/{task_id}/result")

    # ── Agent Directory + Relay ──────────────────────────────────────────

    def list_agents(self) -> list[dict]:
        """List all public agents in the directory."""
        return self._request("GET", "/api/v1/agents")

    def invoke_agent(self, target_agent_id: str, task: str,
                     executor_model: str = "") -> dict:
        """Invoke another agent via relay. Returns {task_id, relay, ...}.

        The calling agent pays all tolls.
        """
        payload: dict = {"task": task}
        if executor_model:
            payload["executor_model"] = executor_model
        return self._request("POST", f"/api/v1/agents/{target_agent_id}/invoke",
                             json=payload)

    # ── Toll Info ────────────────────────────────────────────────────────

    def get_rates(self) -> dict:
        """Get current toll rate schedule (no auth required)."""
        return self._request("GET", "/api/v1/toll/rates")

    def get_estimate(self, task: str = "") -> dict:
        """Get cost estimate for a task."""
        return self._request("GET", f"/api/v1/toll/estimate?task={task}")
