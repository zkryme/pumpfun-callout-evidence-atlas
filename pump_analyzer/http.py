from __future__ import annotations

from collections import deque
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import logging
import random
import time

from .storage import Database, JsonCache


LOG = logging.getLogger(__name__)


class ApiError(RuntimeError):
    def __init__(self, service: str, endpoint: str, message: str, status: int | None = None):
        super().__init__(f"{service} {endpoint}: {message}")
        self.service, self.endpoint, self.status = service, endpoint, status


class HttpJsonClient:
    def __init__(self, service: str, base_url: str, cache: JsonCache, db: Database,
                 headers: dict[str, str] | None = None, requests_per_minute: int = 60,
                 max_retries: int = 4, minimum_interval_seconds: float = 0.0):
        self.service = service
        self.base_url = base_url.rstrip("/")
        self.cache = cache
        self.db = db
        self.headers = {"Accept": "application/json", "User-Agent": "pump-callout-analyzer/0.1"}
        self.headers.update(headers or {})
        self.requests_per_minute = max(1, requests_per_minute)
        self.max_retries = max_retries
        self.minimum_interval_seconds = max(0.0, minimum_interval_seconds)
        self._last_request_at: float | None = None
        self._request_times: deque[float] = deque()

    def _rate_limit(self) -> None:
        now = time.monotonic()
        if self._last_request_at is not None and self.minimum_interval_seconds:
            wait = self.minimum_interval_seconds - (now - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
        while self._request_times and now - self._request_times[0] >= 60:
            self._request_times.popleft()
        if len(self._request_times) >= self.requests_per_minute:
            wait = 60 - (now - self._request_times[0]) + 0.05
            if wait > 0:
                LOG.info("%s rate limit: waiting %.1fs", self.service, wait)
                time.sleep(wait)
        self._request_times.append(time.monotonic())
        self._last_request_at = time.monotonic()

    def get(self, endpoint: str, params: dict[str, Any] | None = None,
            *, use_cache: bool = True) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        cached = self.cache.get(self.service, endpoint, params) if use_cache else None
        if cached is not None:
            return cached
        query = urlencode(params, doseq=True)
        url = f"{self.base_url}/{endpoint.lstrip('/')}" + (f"?{query}" if query else "")
        last_error = "unknown error"
        last_status: int | None = None
        for attempt in range(1, self.max_retries + 1):
            self._rate_limit()
            try:
                request = Request(url, headers=self.headers, method="GET")
                with urlopen(request, timeout=45) as response:
                    body = json.loads(response.read().decode("utf-8"))
                self.cache.put(self.service, endpoint, params, body)
                return body
            except HTTPError as exc:
                last_status = exc.code
                raw = exc.read().decode("utf-8", "replace")
                last_error = raw[:2000]
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt == self.max_retries:
                    break
                retry_after = exc.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** (attempt - 1)
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                if attempt == self.max_retries:
                    break
                delay = 2 ** (attempt - 1)
            delay = delay + random.random() * 0.25
            LOG.warning("%s request failed (attempt %s/%s); retrying in %.1fs",
                        self.service, attempt, self.max_retries, delay)
            time.sleep(delay)
        self.db.record_failure(self.service, endpoint, params, last_status, last_error, self.max_retries)
        raise ApiError(self.service, endpoint, last_error, last_status)

    def post(self, endpoint: str, payload: dict[str, Any], *, use_cache: bool = True) -> Any:
        cache_params = {"_post": payload}
        cached = self.cache.get(self.service, endpoint, cache_params) if use_cache else None
        if cached is not None:
            return cached
        # Query-string-authenticated RPC roots (for example Helius) must not
        # receive a slash after the API key.
        url = self.base_url if not endpoint and "?" in self.base_url else f"{self.base_url}/{endpoint.lstrip('/')}"
        data = json.dumps(payload).encode("utf-8")
        headers = {**self.headers, "Content-Type": "application/json"}
        last_error, last_status = "unknown error", None
        for attempt in range(1, self.max_retries + 1):
            self._rate_limit()
            try:
                request = Request(url, data=data, headers=headers, method="POST")
                with urlopen(request, timeout=60) as response:
                    body = json.loads(response.read().decode("utf-8"))
                if body.get("error"):
                    raise ApiError(self.service, endpoint, json.dumps(body["error"]))
                self.cache.put(self.service, endpoint, cache_params, body)
                return body
            except ApiError:
                raise
            except HTTPError as exc:
                last_status = exc.code
                last_error = exc.read().decode("utf-8", "replace")[:2000]
                if not (exc.code == 429 or 500 <= exc.code < 600) or attempt == self.max_retries:
                    break
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                if attempt == self.max_retries:
                    break
            time.sleep(2 ** (attempt - 1) + random.random() * 0.25)
        self.db.record_failure(self.service, endpoint, cache_params, last_status, last_error, self.max_retries)
        raise ApiError(self.service, endpoint, last_error, last_status)
