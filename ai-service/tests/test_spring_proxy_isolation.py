"""Exercise the Spring adapters over HTTP with a broken ambient proxy."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest

from app.context.identity import UserIdentityProvider
from app.context.music import MusicContextProvider, MusicContextProviderError
from app.tools.music import MusicServiceClient, MusicServiceError


@pytest.fixture
def spring_server(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.server.requests.append((self.path, self.headers.get("Cookie")))
            if self.path == "/api/user/profile":
                data = {"userId": 42}
            elif self.path == "/api/songs/185809/context":
                data = {
                    "songId": 185809, "name": "晴天", "artist": "周杰伦",
                    "album": "叶惠美", "originalLyrics": "",
                    "translatedLyrics": "", "lyricsStatus": "NO_LYRICS",
                }
            else:
                data = []
            body = json.dumps({"code": self.server.status, "message": "test", "data": data}).encode()
            self.send_response(self.server.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests, server.status = [], 200
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    settings = SimpleNamespace(
        spring_boot_base_url=f"http://127.0.0.1:{server.server_port}",
        spring_boot_timeout_seconds=2,
    )
    for module in ("app.context.identity", "app.context.music", "app.tools.music"):
        monkeypatch.setattr(module + ".get_settings", lambda: settings)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "")
    try:
        yield server
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()


async def invoke(adapter):
    if adapter == "identity":
        return await UserIdentityProvider().resolve("JSESSIONID=proxy-isolation-test")
    if adapter == "song":
        return await MusicContextProvider().get_song(185809)
    return await MusicServiceClient().search_music("晴天", limit=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ["identity", "song", "search"])
async def test_spring_requests_bypass_ambient_proxy(spring_server, adapter):
    result = await invoke(adapter)
    assert len(spring_server.requests) == 1
    if adapter == "identity":
        assert result == "42"
        assert spring_server.requests[0] == ("/api/user/profile", "JSESSIONID=proxy-isolation-test")
    elif adapter == "song":
        assert result.song_id == 185809
        assert result.lyrics_status == "NO_LYRICS"
    else:
        assert result.songs == []
        assert result.limit == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ["identity", "song", "search"])
async def test_direct_spring_errors_keep_existing_failure_contract(spring_server, adapter):
    spring_server.status = 503
    if adapter == "identity":
        assert await invoke(adapter) is None
    else:
        error = MusicContextProviderError if adapter == "song" else MusicServiceError
        with pytest.raises(error):
            await invoke(adapter)
    assert len(spring_server.requests) == 1
