"""Rate-limited, retrying HTTPS GET requests shared by the API clients.

Errors never carry the request URL, credentials, or server error text: the
caller supplies the exception types and a service name for the messages.
"""

from __future__ import annotations

import logging
import math
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)
RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

HttpTransport = Callable[[str, float], bytes]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]


class RateLimitScheduler:
    """Reserve rate-limited request slots for one process-wide scope."""

    def __init__(self) -> None:
        self.lock = Lock()
        self.next_request_at: float | None = None

    def reserve_slot(
        self,
        clock: Clock,
        sleeper: Sleeper,
        requests_per_second: float,
    ) -> None:
        with self.lock:
            now = clock()
            if self.next_request_at is not None and now < self.next_request_at:
                sleeper(self.next_request_at - now)
                now = clock()

            interval = 1 / requests_per_second
            scheduled_from = max(now, self.next_request_at or now)
            self.next_request_at = scheduled_from + interval


_process_rate_limiters: dict[str, RateLimitScheduler] = {}
_process_rate_limiters_lock = Lock()


def process_rate_limiter(scope: str) -> RateLimitScheduler:
    """Return the scheduler every client in this process shares for ``scope``."""
    with _process_rate_limiters_lock:
        limiter = _process_rate_limiters.get(scope)
        if limiter is None:
            limiter = _process_rate_limiters[scope] = RateLimitScheduler()
        return limiter


def reset_process_rate_limiters_for_tests() -> None:
    """Clear process-shared limiter state for isolated deterministic tests."""
    with _process_rate_limiters_lock:
        _process_rate_limiters.clear()


@dataclass(frozen=True)
class FetchErrors:
    """The exception types a client wants raised for each kind of failure."""

    timeout: Callable[[str], Exception]
    transport: Callable[[str], Exception]
    http_status: Callable[[int], Exception]


class HttpFetcher:
    """GET URLs with shared rate limiting, bounded retries, and safe errors."""

    def __init__(
        self,
        *,
        service_name: str,
        errors: FetchErrors,
        rate_limit_scope: str,
        requests_per_second: float,
        timeout_seconds: float,
        max_retries: int,
        retry_backoff_seconds: float,
        transport: HttpTransport | None = None,
        clock: Clock | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        import time

        self._service_name = service_name
        self._errors = errors
        self._requests_per_second = requests_per_second
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._transport = transport or default_transport
        self._clock = clock or time.monotonic
        self._sleeper = sleeper or time.sleep
        self.rate_limiter = process_rate_limiter(rate_limit_scope)

    def get(self, url: str) -> bytes:
        """Return the response body, retrying transient failures."""
        retries_used = 0
        while True:
            self.wait_for_request_slot()
            try:
                return self._transport(url, self._timeout_seconds)
            except HTTPError as error:
                if self._should_retry(retries_used, error.code):
                    self._wait_before_retry(retries_used, retry_after_seconds(error))
                    retries_used += 1
                    continue
                raise self._errors.http_status(error.code) from None
            except (TimeoutError, socket.timeout) as error:
                if self._should_retry(retries_used):
                    self._wait_before_retry(retries_used)
                    retries_used += 1
                    continue
                raise self._timeout() from error
            except URLError as error:
                if self._should_retry(retries_used) and not _is_certificate_error(
                    error.reason
                ):
                    self._wait_before_retry(retries_used)
                    retries_used += 1
                    continue
                if isinstance(error.reason, (TimeoutError, socket.timeout)):
                    raise self._timeout() from error
                raise self._unreachable() from error
            except OSError as error:
                if self._should_retry(retries_used) and not _is_certificate_error(
                    error
                ):
                    self._wait_before_retry(retries_used)
                    retries_used += 1
                    continue
                raise self._unreachable() from error

    def wait_for_request_slot(self) -> None:
        self.rate_limiter.reserve_slot(
            self._clock, self._sleeper, self._requests_per_second
        )

    def _timeout(self) -> Exception:
        return self._errors.timeout(f"{self._service_name} request timed out.")

    def _unreachable(self) -> Exception:
        return self._errors.transport(
            f"Unable to reach the {self._service_name} API."
        )

    def _should_retry(self, retries_used: int, status_code: int | None = None) -> bool:
        if retries_used >= self._max_retries:
            return False
        return status_code is None or status_code in RETRYABLE_HTTP_STATUS_CODES

    def _wait_before_retry(
        self, retries_used: int, retry_after: float | None = None
    ) -> None:
        backoff_seconds = self._retry_backoff_seconds * (2 ** retries_used)
        wait_seconds = max(backoff_seconds, retry_after or 0.0)
        LOGGER.warning(
            "Retrying a transient %s request failure.", self._service_name
        )
        self._sleeper(wait_seconds)


def default_transport(url: str, timeout_seconds: float) -> bytes:
    request = Request(url, headers={"User-Agent": "healthcare-research-intelligence"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def retry_after_seconds(error: HTTPError) -> float | None:
    if error.headers is None:
        return None
    value = error.headers.get("Retry-After")
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


def _is_certificate_error(error: object) -> bool:
    # A failed certificate check will fail the same way on every retry.
    return isinstance(error, ssl.SSLCertVerificationError)
