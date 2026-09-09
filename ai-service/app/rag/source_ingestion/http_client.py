"""Policy-aware HTTP transport used by all remote raw fetchers."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
from protego import Protego


DEFAULT_USER_AGENT = "MoodFlowMusicRAG/1.0 (https://github.com/Yee-Ning/MoodFlow)"
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 10
MAX_SINGLE_SLEEP_SECONDS = 60.0


class RobotsDeniedError(PermissionError):
    """The origin's robots policy does not permit this fetch."""


class ResponseTooLargeError(ValueError):
    """The response exceeds the configured raw snapshot size limit."""


@dataclass(frozen=True, slots=True)
class RawHttpResponse:
    content: bytes
    content_type: str
    http_status: int


class PolicyAwareHttpClient:
    """Fetch bytes while enforcing robots.txt, pacing, and a hard size limit."""

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        min_interval_seconds: float = 1.1,
        max_response_bytes: int = 50 * 1024 * 1024,
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        normalized_user_agent = user_agent.strip()
        if not normalized_user_agent:
            raise ValueError("user_agent must not be blank")
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be non-negative")
        if min_interval_seconds > MAX_SINGLE_SLEEP_SECONDS:
            raise ValueError(
                f"min_interval_seconds must not exceed {MAX_SINGLE_SLEEP_SECONDS:g}"
            )
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")

        self.user_agent = normalized_user_agent
        self.min_interval_seconds = min_interval_seconds
        self.max_response_bytes = max_response_bytes
        self._client = client or httpx.Client(
            follow_redirects=True,
            timeout=timeout_seconds,
            headers={"User-Agent": normalized_user_agent, "Accept": "*/*"},
        )
        self._owns_client = client is None
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._robots_cache: dict[str, Protego | bool] = {}
        self._crawl_delay_by_origin: dict[str, float] = {}
        self._last_request_at: dict[str, float] = {}

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "PolicyAwareHttpClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"unsupported source URL: {url}")
        return f"{parsed.scheme}://{parsed.netloc}"

    def _pace(self, origin: str) -> None:
        previous = self._last_request_at.get(origin)
        if previous is not None:
            required_interval = max(
                self.min_interval_seconds,
                self._crawl_delay_by_origin.get(origin, 0),
            )
            remaining = required_interval - (self._monotonic() - previous)
            if remaining > 0:
                if remaining > MAX_SINGLE_SLEEP_SECONDS:
                    raise RobotsDeniedError(
                        "robots crawl delay exceeds the supported 60-second wait; "
                        "fetch aborted"
                    )
                self._sleeper(remaining)
        self._last_request_at[origin] = self._monotonic()

    def _request_robots(self, robots_url: str) -> httpx.Response | None:
        current_url = robots_url
        for _ in range(MAX_REDIRECTS + 1):
            origin = self._origin(current_url)
            self._pace(origin)
            try:
                response = self._client.get(
                    current_url,
                    headers={
                        "User-Agent": self.user_agent,
                        "Accept": "text/plain,*/*;q=0.1",
                    },
                    follow_redirects=False,
                )
            except httpx.HTTPError:
                return None
            if response.status_code not in REDIRECT_STATUSES:
                return response
            location = response.headers.get("Location")
            if not location:
                return None
            current_url = urljoin(current_url, location)
        return None

    def _load_robots_policy(self, url: str) -> Protego | bool:
        origin = self._origin(url)
        cached = self._robots_cache.get(origin)
        if cached is not None:
            return cached

        robots_url = f"{origin}/robots.txt"
        response = self._request_robots(robots_url)
        if response is None:
            self._robots_cache[origin] = False
            return False

        if response.status_code == 200:
            parser = Protego.parse(response.text)
            self._robots_cache[origin] = parser
            agent_token = self.user_agent.partition("/")[0]
            crawl_delay = parser.crawl_delay(agent_token)
            if crawl_delay is not None:
                self._crawl_delay_by_origin[origin] = float(crawl_delay)
            return parser
        if response.status_code in {401, 403} or response.status_code >= 500:
            self._robots_cache[origin] = False
            return False

        self._robots_cache[origin] = True
        return True

    def _ensure_robots_allowed(self, url: str) -> None:
        policy = self._load_robots_policy(url)
        agent_token = self.user_agent.partition("/")[0]
        allowed = policy if isinstance(policy, bool) else policy.can_fetch(url, agent_token)
        if not allowed:
            raise RobotsDeniedError(f"robots.txt does not allow fetching {url}")

    def fetch(self, url: str) -> RawHttpResponse:
        current_url = url
        for _ in range(MAX_REDIRECTS + 1):
            self._ensure_robots_allowed(current_url)
            origin = self._origin(current_url)
            self._pace(origin)

            with self._client.stream(
                "GET",
                current_url,
                headers={"User-Agent": self.user_agent, "Accept": "*/*"},
                follow_redirects=False,
            ) as response:
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        raise httpx.HTTPStatusError(
                            "redirect response is missing a Location header",
                            request=response.request,
                            response=response,
                        )
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()
                content_length = response.headers.get("Content-Length")
                if (
                    content_length is not None
                    and int(content_length) > self.max_response_bytes
                ):
                    raise ResponseTooLargeError(
                        f"response Content-Length exceeds {self.max_response_bytes} bytes"
                    )

                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > self.max_response_bytes:
                        raise ResponseTooLargeError(
                            f"response body exceeds {self.max_response_bytes} bytes"
                        )
                    chunks.append(chunk)

                return RawHttpResponse(
                    content=b"".join(chunks),
                    content_type=response.headers.get(
                        "Content-Type", "application/octet-stream"
                    ),
                    http_status=response.status_code,
                )
        raise httpx.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects for {url}")
