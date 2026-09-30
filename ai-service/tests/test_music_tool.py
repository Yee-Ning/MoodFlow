import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.tools.music import MusicServiceClient, MusicServiceError, search_music


def fake_settings() -> SimpleNamespace:
    return SimpleNamespace(
        spring_boot_base_url="http://127.0.0.1:8080",
        spring_boot_timeout_seconds=10,
    )


def run_with_transport(handler: httpx.MockTransport, awaitable_factory):
    client = httpx.AsyncClient(transport=handler)
    with (
        patch("app.tools.music.httpx.AsyncClient", return_value=client),
        patch("app.tools.music.get_settings", fake_settings),
    ):
        return asyncio.run(awaitable_factory())


class MusicServiceClientTests(unittest.TestCase):
    def test_search_music_returns_normalized_ids_and_fields(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/api/music/search")
            self.assertEqual(request.url.params["keyword"], "周杰伦")
            self.assertEqual(request.url.params["limit"], "2")
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "message": "success",
                    "data": [
                        {
                            "id": 185809,
                            "name": "晴天",
                            "artists": ["周杰伦"],
                            "album": "叶惠美",
                            "coverUrl": "https://img.test/qingtian.jpg",
                            "duration": 269000,
                        }
                    ],
                },
            )

        result = run_with_transport(
            httpx.MockTransport(handler),
            lambda: search_music("周杰伦", 2),
        )

        self.assertEqual(result.keyword, "周杰伦")
        self.assertEqual(result.limit, 2)
        self.assertEqual(result.songs[0].song_id, 185809)
        self.assertEqual(result.songs[0].artists, ["周杰伦"])
        serialized = result.model_dump(mode="json")
        self.assertIn("song_id", serialized["songs"][0])
        self.assertNotIn("id", serialized["songs"][0])

    def test_get_song_lyrics_uses_backend_and_session(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/songs/185809/lyrics")
            self.assertEqual(request.headers["cookie"], "JSESSIONID=session-1")
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "message": "success",
                    "data": {
                        "lyric": "故事的小黄花",
                        "translation": "",
                        "status": "available",
                    },
                },
            )

        result = run_with_transport(
            httpx.MockTransport(handler),
            lambda: MusicServiceClient("JSESSIONID=session-1").get_song_lyrics(185809),
        )

        self.assertEqual(result.song_id, 185809)
        self.assertEqual(result.original_lyrics, "故事的小黄花")
        self.assertEqual(result.status, "available")

    def test_search_entities_normalizes_catalog_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/music/entities/search")
            self.assertEqual(request.url.params["keyword"], "周杰伦")
            self.assertEqual(request.url.params["type"], "artist")
            self.assertEqual(request.url.params["limit"], "3")
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "message": "success",
                    "data": [
                        {
                            "id": 6452,
                            "entityType": "artist",
                            "name": "周杰伦",
                            "artist": "周杰伦",
                            "album": "",
                            "coverUrl": "https://img.test/jay.jpg",
                            "metadata": {"album_size": 40},
                            "relatedEntities": [],
                        }
                    ],
                },
            )

        result = run_with_transport(
            httpx.MockTransport(handler),
            lambda: MusicServiceClient().search_entities(
                "周杰伦",
                entity_type="artist",
                limit=3,
            ),
        )

        self.assertEqual(result[0].entity_id, 6452)
        self.assertEqual(result[0].entity_type, "artist")
        self.assertEqual(result[0].metadata["album_size"], 40)

    def test_search_playlist_entities_and_empty_results(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/music/entities/search")
            self.assertEqual(request.url.params["type"], "playlist")
            return httpx.Response(200, json={
                "code": 200, "message": "success", "data": [{
                    "id": 77, "entityType": "playlist", "name": "学习歌单",
                    "artist": "测试用户", "album": "", "coverUrl": "",
                    "metadata": {"track_count": 2}, "relatedEntities": [],
                }] if request.url.params["keyword"] == "学习" else [],
            })

        def search(query: str):
            return MusicServiceClient().search_entities(query, entity_type="playlist")

        result = run_with_transport(httpx.MockTransport(handler), lambda: search("学习"))
        self.assertEqual((result[0].entity_id, result[0].entity_type), (77, "playlist"))
        empty = run_with_transport(httpx.MockTransport(handler), lambda: search("不存在"))
        self.assertEqual(empty, [])

    def test_get_playlist_detail_returns_all_songs_with_session(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/playlists/77")
            self.assertEqual(request.headers["cookie"], "JSESSIONID=session-4")
            return httpx.Response(200, json={
                "code": 200, "message": "success", "data": {
                    "playlist": {
                        "id": 77, "name": "学习歌单", "coverImgUrl": "",
                        "trackCount": 1, "creator": "测试用户", "subscribed": False,
                    },
                    "description": "专注时听", "songs": [{
                        "id": 185809, "name": "晴天", "artists": ["周杰伦"],
                        "album": "叶惠美", "coverUrl": "", "duration": 269000,
                    }],
                },
            })

        result = run_with_transport(
            httpx.MockTransport(handler),
            lambda: MusicServiceClient("JSESSIONID=session-4").get_playlist_detail(77),
        )
        self.assertEqual(result.name, "学习歌单")
        self.assertEqual(result.songs[0].song_id, 185809)
        self.assertEqual(result.songs[0].artists, ["周杰伦"])

    def test_get_album_detail_returns_complete_tracks(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/albums/18905")
            return httpx.Response(200, json={
                "code": 200, "message": "success", "data": {
                    "album": {
                        "id": 18905, "entityType": "album", "name": "叶惠美",
                        "artist": "周杰伦", "album": "叶惠美", "coverUrl": "",
                        "metadata": {"size": 2}, "relatedEntities": [],
                    },
                    "songs": [
                        {"id": 185809, "name": "晴天", "artists": ["周杰伦"],
                         "album": "叶惠美", "coverUrl": "", "duration": 269000},
                        {"id": 185811, "name": "以父之名", "artists": ["周杰伦"],
                         "album": "叶惠美", "coverUrl": "", "duration": 342000},
                    ],
                },
            })

        result = run_with_transport(
            httpx.MockTransport(handler),
            lambda: MusicServiceClient().get_album_detail(18905),
        )

        self.assertEqual(result.album_id, 18905)
        self.assertEqual(result.name, "叶惠美")
        self.assertEqual([song.song_id for song in result.songs], [185809, 185811])

    def test_get_playlist_detail_rejects_wrong_id_and_invalid_argument(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "code": 200, "message": "success", "data": {
                    "playlist": {
                        "id": 88, "name": "其他歌单", "coverImgUrl": "",
                        "trackCount": 0, "creator": "测试用户", "subscribed": False,
                    }, "description": "", "songs": [],
                },
            })

        empty = run_with_transport(
            httpx.MockTransport(handler),
            lambda: MusicServiceClient().get_playlist_detail(88),
        )
        self.assertEqual(empty.songs, [])
        with self.assertRaises(MusicServiceError) as raised:
            run_with_transport(
                httpx.MockTransport(handler),
                lambda: MusicServiceClient().get_playlist_detail(77),
            )
        self.assertEqual(raised.exception.code, "invalid_response")
        with self.assertRaises(ValueError):
            asyncio.run(MusicServiceClient().get_playlist_detail(0))

    def test_entity_detail_rejects_mismatched_backend_entity(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/music/entities/song/185809")
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "message": "success",
                    "data": {
                        "id": 99,
                        "entityType": "song",
                        "name": "错误歌曲",
                        "artist": "测试歌手",
                        "album": "测试专辑",
                        "coverUrl": "",
                        "metadata": {},
                        "relatedEntities": [],
                    },
                },
            )

        with self.assertRaises(MusicServiceError) as raised:
            run_with_transport(
                httpx.MockTransport(handler),
                lambda: MusicServiceClient().get_entity_detail("song", 185809),
            )

        self.assertEqual(raised.exception.code, "invalid_response")

    def test_get_user_playlists_returns_normalized_playlist_ids(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/user/playlists")
            self.assertEqual(request.headers["cookie"], "JSESSIONID=session-2")
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "message": "success",
                    "data": [
                        {
                            "id": 77,
                            "name": "深夜学习",
                            "coverImgUrl": "https://img.test/playlist.jpg",
                            "trackCount": 12,
                            "creator": "测试用户",
                            "subscribed": False,
                        },
                        {
                            "id": 88,
                            "name": "收藏歌单",
                            "coverImgUrl": "",
                            "trackCount": 5,
                            "creator": "别人",
                            "subscribed": True,
                        },
                    ],
                },
            )

        result = run_with_transport(
            httpx.MockTransport(handler),
            lambda: MusicServiceClient("JSESSIONID=session-2").get_user_playlists(),
        )

        self.assertEqual(result.playlists[0].playlist_id, 77)
        self.assertEqual(result.playlists[0].name, "深夜学习")
        self.assertTrue(result.playlists[0].editable)
        self.assertFalse(result.playlists[1].editable)

    def test_add_song_to_playlist_posts_ids_and_uses_session(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/api/playlists/77/songs/33")
            self.assertEqual(request.headers["cookie"], "JSESSIONID=session-3")
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "message": "success",
                    "data": {"status": "added", "message": "已添加到歌单"},
                },
            )

        result = run_with_transport(
            httpx.MockTransport(handler),
            lambda: MusicServiceClient("JSESSIONID=session-3").add_song_to_playlist(
                77,
                33,
            ),
        )

        self.assertEqual(result.playlist_id, 77)
        self.assertEqual(result.song_id, 33)
        self.assertEqual(result.status, "added")

    def test_preserves_backend_business_error_as_safe_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                json={"code": 401, "message": "请先登录网易云音乐"},
            )

        with self.assertRaises(MusicServiceError) as raised:
            run_with_transport(
                httpx.MockTransport(handler),
                lambda: MusicServiceClient().get_user_playlists(),
            )

        self.assertEqual(raised.exception.code, "http_401")
        self.assertIn("请先登录", raised.exception.public_message)

    def test_handles_timeout_without_leaking_transport_details(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("private transport detail", request=request)

        with self.assertRaises(MusicServiceError) as raised:
            run_with_transport(
                httpx.MockTransport(handler),
                lambda: search_music("周杰伦"),
            )

        self.assertEqual(raised.exception.code, "timeout")
        self.assertNotIn("private transport detail", str(raised.exception))

    def test_rejects_invalid_backend_data(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "message": "success",
                    "data": [{"id": 1, "name": "Broken"}],
                },
            )

        with self.assertRaises(MusicServiceError) as raised:
            run_with_transport(
                httpx.MockTransport(handler),
                lambda: search_music("周杰伦"),
            )

        self.assertEqual(raised.exception.code, "invalid_response")


if __name__ == "__main__":
    unittest.main()
