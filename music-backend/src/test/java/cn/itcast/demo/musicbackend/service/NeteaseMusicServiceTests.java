package cn.itcast.demo.musicbackend.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.http.MediaType.APPLICATION_JSON;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withServerError;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

import java.net.URI;
import java.util.Arrays;
import java.util.List;
import java.util.Map;

import cn.itcast.demo.musicbackend.dto.CurrentSongContextData;
import cn.itcast.demo.musicbackend.dto.AlbumDetailData;
import cn.itcast.demo.musicbackend.dto.LyricsStatus;
import cn.itcast.demo.musicbackend.dto.MusicEntityData;
import cn.itcast.demo.musicbackend.dto.PlaylistData;
import cn.itcast.demo.musicbackend.dto.PlaylistDetailData;
import cn.itcast.demo.musicbackend.dto.PlaylistMutationData;
import cn.itcast.demo.musicbackend.dto.SongData;
import cn.itcast.demo.musicbackend.dto.SongLyricsData;
import cn.itcast.demo.musicbackend.dto.SongPlaybackData;
import cn.itcast.demo.musicbackend.dto.UserProfileData;
import cn.itcast.demo.musicbackend.exception.BadRequestException;
import cn.itcast.demo.musicbackend.exception.NotFoundException;
import cn.itcast.demo.musicbackend.exception.NeteaseApiException;
import cn.itcast.demo.musicbackend.exception.UnauthorizedException;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpHeaders;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

class NeteaseMusicServiceTests {

    private static final String COOKIE = "MUSIC_U=test-cookie; Max-Age=1296000; Path=/;;__csrf=test-csrf; Path=/;";
    private static final String FORWARDED_COOKIE = "MUSIC_U=test-cookie; __csrf=test-csrf";

    private MockRestServiceServer server;
    private NeteaseMusicService musicService;

    @BeforeEach
    void setUp() {
        RestClient.Builder builder = RestClient.builder().baseUrl("http://localhost:3000");
        server = MockRestServiceServer.bindTo(builder).build();
        musicService = new NeteaseMusicService(builder.build());
    }

    @Test
    void shouldRequireExistingLoginSession() {
        MockHttpServletRequest request = new MockHttpServletRequest();

        UnauthorizedException exception = assertThrows(
                UnauthorizedException.class,
                () -> musicService.getCurrentUserProfile(request));

        assertEquals("请先登录网易云音乐", exception.getMessage());
        server.verify();
    }

    @Test
    void shouldMapCurrentUserProfileAndForwardCookie() {
        server.expect(request -> {
                    assertRequest(request.getURI(), "/user/account");
                    assertEquals(FORWARDED_COOKIE, request.getHeaders().getFirst(HttpHeaders.COOKIE));
                })
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "profile": {
                            "userId": 42,
                            "nickname": "Deep Tide",
                            "avatarUrl": "https://img.test/avatar.jpg",
                            "ignored": "not exposed"
                          }
                        }
                        """, APPLICATION_JSON));

        UserProfileData profile = musicService.getCurrentUserProfile(loggedInRequest());

        assertEquals(42L, profile.userId());
        assertEquals("Deep Tide", profile.nickname());
        assertEquals("https://img.test/avatar.jpg", profile.avatarUrl());
        server.verify();
    }

    @Test
    void shouldSplitPlaylistsUsingSubscribedField() {
        expectProfile();
        server.expect(request -> {
                    assertRequest(request.getURI(), "/user/playlist", "uid=42", "limit=1000", "offset=0");
                    assertEquals(FORWARDED_COOKIE, request.getHeaders().getFirst(HttpHeaders.COOKIE));
                })
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "more": false,
                          "playlist": [
                            {
                              "id": 101,
                              "name": "Created Waves",
                              "coverImgUrl": "https://img.test/created.jpg",
                              "trackCount": 12,
                              "creator": {"nickname": "Deep Tide"},
                              "subscribed": false,
                              "description": "not exposed"
                            },
                            {
                              "id": 202,
                              "name": "Collected Foam",
                              "coverImgUrl": "https://img.test/collected.jpg",
                              "trackCount": 8,
                              "creator": {"nickname": "Another Listener"},
                              "subscribed": true
                            }
                          ]
                        }
                        """, APPLICATION_JSON));

        List<PlaylistData> playlists = musicService.getCurrentUserPlaylists(loggedInRequest());

        assertEquals(2, playlists.size());
        assertFalse(playlists.get(0).subscribed());
        assertTrue(playlists.get(1).subscribed());
        assertEquals("Another Listener", playlists.get(1).creator());
        server.verify();
    }

    @Test
    void shouldSearchSongsWithoutRequiringLoginSession() {
        server.expect(request -> {
                    assertRequest(
                            request.getURI(),
                            "/cloudsearch",
                            "keywords=周杰伦",
                            "type=1",
                            "limit=2");
                    assertNull(request.getHeaders().getFirst(HttpHeaders.COOKIE));
                })
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "result": {
                            "songCount": 1,
                            "songs": [
                              {
                                "id": 185809,
                                "name": "晴天",
                                "ar": [{"id": 6452, "name": "周杰伦"}],
                                "al": {
                                  "id": 18905,
                                  "name": "叶惠美",
                                  "picUrl": "https://img.test/qingtian.jpg"
                                },
                                "dt": 269000
                              }
                            ]
                          }
                        }
                        """, APPLICATION_JSON));

        List<SongData> songs = musicService.searchSongs(" 周杰伦 ", 2);

        assertEquals(1, songs.size());
        assertEquals(185809L, songs.getFirst().id());
        assertEquals("晴天", songs.getFirst().name());
        assertEquals(List.of("周杰伦"), songs.getFirst().artists());
        assertEquals("叶惠美", songs.getFirst().album());
        server.verify();
    }

    @Test
    void shouldSearchArtistEntitiesWithoutRequiringLoginSession() {
        server.expect(request -> {
                    assertRequest(
                            request.getURI(),
                            "/cloudsearch",
                            "keywords=周杰伦",
                            "type=100",
                            "limit=3");
                    assertNull(request.getHeaders().getFirst(HttpHeaders.COOKIE));
                })
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "result": {
                            "artistCount": 1,
                            "artists": [
                              {
                                "id": 6452,
                                "name": "周杰伦",
                                "picUrl": "https://img.test/jay.jpg",
                                "alias": ["Jay Chou"],
                                "albumSize": 40,
                                "musicSize": 600
                              }
                            ]
                          }
                        }
                        """, APPLICATION_JSON));

        List<MusicEntityData> entities = musicService.searchMusicEntities(
                " 周杰伦 ",
                "artist",
                3);

        assertEquals(1, entities.size());
        MusicEntityData artist = entities.getFirst();
        assertEquals(6452L, artist.id());
        assertEquals("artist", artist.entityType());
        assertEquals("周杰伦", artist.name());
        assertEquals(List.of("Jay Chou"), artist.metadata().get("aliases"));
        assertTrue(artist.relatedEntities().isEmpty());
        server.verify();
    }

    @Test
    void shouldLoadAlbumEntityWithGroundedRelatedSongs() {
        server.expect(request -> {
                    assertRequest(request.getURI(), "/album", "id=18905");
                    assertNull(request.getHeaders().getFirst(HttpHeaders.COOKIE));
                })
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "album": {
                            "id": 18905,
                            "name": "叶惠美",
                            "picUrl": "https://img.test/album.jpg",
                            "publishTime": 1059955200000,
                            "company": "杰威尔音乐",
                            "description": "录音室专辑",
                            "size": 11,
                            "artists": [{"id": 6452, "name": "周杰伦"}]
                          },
                          "songs": [
                            {
                              "id": 185809,
                              "name": "晴天",
                              "ar": [{"id": 6452, "name": "周杰伦"}],
                              "al": {"id": 18905, "name": "叶惠美"}
                            }
                          ]
                        }
                        """, APPLICATION_JSON));

        MusicEntityData album = musicService.getMusicEntityDetail("album", 18905);

        assertEquals("album", album.entityType());
        assertEquals("叶惠美", album.name());
        assertEquals("周杰伦", album.artist());
        assertEquals(2, album.relatedEntities().size());
        assertEquals("artist", album.relatedEntities().get(0).entityType());
        assertEquals("song", album.relatedEntities().get(1).entityType());
        assertEquals(1059955200000L, album.metadata().get("publish_time"));
        server.verify();
    }

    @Test
    void shouldLoadCompleteAlbumTracksForQueuePlayback() {
        server.expect(request -> {
                    assertRequest(request.getURI(), "/album", "id=18905");
                    assertNull(request.getHeaders().getFirst(HttpHeaders.COOKIE));
                })
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "album": {
                            "id": 18905,
                            "name": "叶惠美",
                            "picUrl": "https://img.test/album.jpg",
                            "artists": [{"id": 6452, "name": "周杰伦"}]
                          },
                          "songs": [
                            {
                              "id": 185809,
                              "name": "晴天",
                              "ar": [{"id": 6452, "name": "周杰伦"}],
                              "al": {"id": 18905, "name": "叶惠美", "picUrl": "https://img.test/album.jpg"},
                              "dt": 269000
                            },
                            {
                              "id": 185811,
                              "name": "以父之名",
                              "ar": [{"id": 6452, "name": "周杰伦"}],
                              "al": {"id": 18905, "name": "叶惠美", "picUrl": "https://img.test/album.jpg"},
                              "dt": 342000
                            }
                          ]
                        }
                        """, APPLICATION_JSON));

        AlbumDetailData detail = musicService.getAlbumDetail(18905);

        assertEquals(18905L, detail.album().id());
        assertEquals("叶惠美", detail.album().name());
        assertEquals(List.of(185809L, 185811L),
                detail.songs().stream().map(SongData::id).toList());
        assertEquals(269000L, detail.songs().getFirst().duration());
        server.verify();
    }

    @Test
    void shouldSearchPlaylistEntities() {
        server.expect(request -> {
                    assertRequest(request.getURI(), "/cloudsearch",
                            "keywords=学习", "type=1000", "limit=3");
                    assertNull(request.getHeaders().getFirst(HttpHeaders.COOKIE));
                })
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "result": {
                            "playlistCount": 1,
                            "playlists": [{
                              "id": 77,
                              "name": "学习歌单",
                              "coverImgUrl": "https://img.test/playlist.jpg",
                              "trackCount": 2,
                              "creator": {"nickname": "测试用户"}
                            }]
                          }
                        }
                        """, APPLICATION_JSON));

        List<MusicEntityData> entities = musicService.searchMusicEntities("学习", "playlist", 3);

        assertEquals(1, entities.size());
        assertEquals(77L, entities.getFirst().id());
        assertEquals("playlist", entities.getFirst().entityType());
        assertEquals("学习歌单", entities.getFirst().name());
        assertEquals(2, entities.getFirst().metadata().get("track_count"));
        server.verify();
    }

    @Test
    void shouldRejectUnsupportedEntityTypeBeforeCallingUpstream() {
        assertThrows(
                BadRequestException.class,
                () -> musicService.searchMusicEntities("周杰伦", "genre", 3));
        server.verify();
    }

    @Test
    void shouldGetSongDetailWithoutRequiringLoginSession() {
        server.expect(request -> {
                    assertRequest(request.getURI(), "/song/detail", "ids=185809");
                    assertNull(request.getHeaders().getFirst(HttpHeaders.COOKIE));
                })
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "songs": [
                            {
                              "id": 185809,
                              "name": "晴天",
                              "ar": [{"id": 6452, "name": "周杰伦"}],
                              "al": {
                                "id": 18905,
                                "name": "叶惠美",
                                "picUrl": "https://img.test/qingtian.jpg"
                              },
                              "dt": 269000
                            }
                          ]
                        }
                        """, APPLICATION_JSON));

        SongData song = musicService.getSongDetail(185809);

        assertEquals(185809L, song.id());
        assertEquals("晴天", song.name());
        assertEquals(List.of("周杰伦"), song.artists());
        assertEquals("叶惠美", song.album());
        assertEquals(269000L, song.duration());
        server.verify();
    }

    @Test
    void shouldLoadSongEntityWithSimilarWorksForWorldLine() {
        expectSongDetail(185809, "晴天", "周杰伦", "叶惠美");
        server.expect(request -> {
                    assertRequest(request.getURI(), "/simi/song", "id=185809");
                    assertNull(request.getHeaders().getFirst(HttpHeaders.COOKIE));
                })
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "songs": [
                            {
                              "id": 186016,
                              "name": "断了的弦",
                              "ar": [{"id": 6452, "name": "周杰伦"}],
                              "al": {"id": 18907, "name": "寻找周杰伦"}
                            }
                          ]
                        }
                        """, APPLICATION_JSON));

        MusicEntityData song = musicService.getMusicEntityDetail("song", 185809);

        assertEquals("song", song.entityType());
        assertEquals(3, song.relatedEntities().size());
        assertEquals("artist", song.relatedEntities().get(0).entityType());
        assertEquals("album", song.relatedEntities().get(1).entityType());
        assertEquals("song", song.relatedEntities().get(2).entityType());
        assertEquals("断了的弦", song.relatedEntities().get(2).name());
        server.verify();
    }

    @Test
    void shouldReportMissingSongDetail() {
        server.expect(request -> assertRequest(
                        request.getURI(),
                        "/song/detail",
                        "ids=999999"))
                .andRespond(withSuccess(
                        "{\"code\":200,\"songs\":[]}",
                        APPLICATION_JSON));

        assertThrows(NotFoundException.class, () -> musicService.getSongDetail(999999));
        server.verify();
    }

    @Test
    void shouldAggregateSongContextWithOriginalLyrics() {
        expectSongDetail(185809, "晴天", "周杰伦", "叶惠美");
        server.expect(request -> {
                    assertRequest(request.getURI(), "/lyric", "id=185809");
                    assertNull(request.getHeaders().getFirst(HttpHeaders.COOKIE));
                })
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "lrc": {"lyric": "[00:01.00]故事的小黄花"}
                        }
                        """, APPLICATION_JSON));

        CurrentSongContextData context = musicService.getCurrentSongContext(185809);

        assertEquals(185809L, context.songId());
        assertEquals("晴天", context.name());
        assertEquals("周杰伦", context.artist());
        assertEquals("叶惠美", context.album());
        assertEquals("[00:01.00]故事的小黄花", context.originalLyrics());
        assertEquals("", context.translatedLyrics());
        assertEquals(LyricsStatus.AVAILABLE, context.lyricsStatus());
        server.verify();
    }

    @Test
    void shouldAggregateTranslatedLyrics() {
        expectSongDetail(9001, "Blue Current", "Low Moon", "Night Water");
        server.expect(request -> assertRequest(request.getURI(), "/lyric", "id=9001"))
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "lrc": {"lyric": "[00:01.00]Blue current"},
                          "tlyric": {"lyric": "[00:01.00]蓝色潮汐"}
                        }
                        """, APPLICATION_JSON));

        CurrentSongContextData context = musicService.getCurrentSongContext(9001);

        assertEquals("[00:01.00]蓝色潮汐", context.translatedLyrics());
        assertEquals(LyricsStatus.AVAILABLE, context.lyricsStatus());
        server.verify();
    }

    @Test
    void shouldIdentifySongContextWithoutLyrics() {
        expectSongDetail(9002, "Still Harbor", "North Wind", "Quiet Coast");
        server.expect(request -> assertRequest(request.getURI(), "/lyric", "id=9002"))
                .andRespond(withSuccess(
                        "{\"code\":200,\"nolyric\":true}",
                        APPLICATION_JSON));

        CurrentSongContextData context = musicService.getCurrentSongContext(9002);

        assertEquals("", context.originalLyrics());
        assertEquals("", context.translatedLyrics());
        assertEquals(LyricsStatus.NO_LYRICS, context.lyricsStatus());
        server.verify();
    }

    @Test
    void shouldKeepSongContextWhenLyricsAreUnavailable() {
        expectSongDetail(9003, "Far Signal", "Quiet Radio", "Static Sea");
        server.expect(request -> assertRequest(request.getURI(), "/lyric", "id=9003"))
                .andRespond(withServerError());

        CurrentSongContextData context = musicService.getCurrentSongContext(9003);

        assertEquals("Far Signal", context.name());
        assertEquals(LyricsStatus.UNAVAILABLE, context.lyricsStatus());
        assertTrue(context.originalLyrics().isEmpty());
        server.verify();
    }

    @Test
    void shouldMapPlaylistDetailAndSongs() {
        server.expect(request -> {
                    assertRequest(request.getURI(), "/playlist/detail", "id=101");
                    assertEquals(FORWARDED_COOKIE, request.getHeaders().getFirst(HttpHeaders.COOKIE));
                })
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "playlist": {
                            "id": 101,
                            "name": "Created Waves",
                            "coverImgUrl": "https://img.test/created.jpg",
                            "trackCount": 2,
                            "creator": {"nickname": "Deep Tide"},
                            "subscribed": false,
                            "description": "Songs for a quiet shoreline."
                          }
                        }
                        """, APPLICATION_JSON));
        server.expect(request -> {
                    assertRequest(
                            request.getURI(),
                            "/playlist/track/all",
                            "id=101",
                            "limit=2",
                            "offset=0");
                    assertEquals(FORWARDED_COOKIE, request.getHeaders().getFirst(HttpHeaders.COOKIE));
                })
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "songs": [
                            {
                              "id": 9001,
                              "name": "Blue Current",
                              "ar": [{"id": 1, "name": "Low Moon"}, {"id": 2, "name": "Sea Glass"}],
                              "al": {"id": 4, "name": "Night Water", "picUrl": "https://img.test/song.jpg"},
                              "dt": 245000,
                              "privilege": {"ignored": true}
                            },
                            {
                              "id": 9002,
                              "name": "Still Harbor",
                              "ar": [{"id": 3, "name": "North Wind"}],
                              "al": {"id": 5, "name": "Quiet Coast", "picUrl": "https://img.test/song-2.jpg"},
                              "dt": 181000
                            }
                          ]
                        }
                        """, APPLICATION_JSON));

        PlaylistDetailData detail = musicService.getPlaylistDetail(101, loggedInRequest());

        assertEquals("Created Waves", detail.playlist().name());
        assertEquals("Songs for a quiet shoreline.", detail.description());
        assertEquals(2, detail.songs().size());
        SongData firstSong = detail.songs().get(0);
        assertEquals(9001L, firstSong.id());
        assertEquals(List.of("Low Moon", "Sea Glass"), firstSong.artists());
        assertEquals("Night Water", firstSong.album());
        assertEquals("https://img.test/song.jpg", firstSong.coverUrl());
        assertEquals(245000L, firstSong.duration());
        server.verify();
    }

    @Test
    void shouldMapPlayableSongUrlAndForwardCookie() {
        server.expect(request -> {
                    assertRequest(
                            request.getURI(),
                            "/song/url/v1",
                            "id=9001",
                            "level=standard");
                    assertEquals(FORWARDED_COOKIE, request.getHeaders().getFirst(HttpHeaders.COOKIE));
                })
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "data": [
                            {
                              "id": 9001,
                              "url": "https://music.test/blue-current.mp3",
                              "type": "mp3"
                            }
                          ]
                        }
                        """, APPLICATION_JSON));

        SongPlaybackData playback = musicService.getSongPlaybackUrl(9001, loggedInRequest());

        assertEquals(9001L, playback.songId());
        assertEquals("https://music.test/blue-current.mp3", playback.url());
        assertTrue(playback.available());
        assertEquals("", playback.message());
        server.verify();
    }

    @Test
    void shouldReturnFriendlyResultWhenSongHasNoPlaybackUrl() {
        server.expect(request -> assertRequest(
                        request.getURI(),
                        "/song/url/v1",
                        "id=9002",
                        "level=standard"))
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "data": [{"id": 9002, "url": null}]
                        }
                        """, APPLICATION_JSON));

        SongPlaybackData playback = musicService.getSongPlaybackUrl(9002, loggedInRequest());

        assertEquals(9002L, playback.songId());
        assertEquals("", playback.url());
        assertFalse(playback.available());
        assertTrue(playback.message().contains("无法播放"));
        server.verify();
    }

    @Test
    void shouldTreatMissingRemoteProfileAsExpiredLogin() {
        server.expect(request -> assertRequest(request.getURI(), "/user/account"))
                .andRespond(withSuccess(
                        "{\"code\":200,\"account\":null,\"profile\":null}",
                        APPLICATION_JSON));

        UnauthorizedException exception = assertThrows(
                UnauthorizedException.class,
                () -> musicService.getCurrentUserProfile(loggedInRequest()));

        assertEquals("网易云登录已失效，请重新扫码登录", exception.getMessage());
        server.verify();
    }

    @Test
    void shouldMapLyricsAndTranslation() {
        server.expect(request -> {
                    assertRequest(request.getURI(), "/lyric", "id=9001");
                    assertEquals(FORWARDED_COOKIE, request.getHeaders().getFirst(HttpHeaders.COOKIE));
                })
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "lrc": {"lyric": "[00:01.00]Blue current"},
                          "tlyric": {"lyric": "[00:01.00]蓝色潮汐"}
                        }
                        """, APPLICATION_JSON));

        SongLyricsData lyrics = musicService.getSongLyrics(9001, loggedInRequest());

        assertEquals("available", lyrics.status());
        assertEquals("[00:01.00]Blue current", lyrics.lyric());
        assertEquals("[00:01.00]蓝色潮汐", lyrics.translation());
        server.verify();
    }

    @Test
    void shouldIdentifyInstrumentalTrack() {
        server.expect(request -> assertRequest(request.getURI(), "/lyric", "id=9002"))
                .andRespond(withSuccess(
                        "{\"code\":200,\"nolyric\":true}",
                        APPLICATION_JSON));

        SongLyricsData lyrics = musicService.getSongLyrics(9002, loggedInRequest());

        assertEquals("instrumental", lyrics.status());
        assertTrue(lyrics.lyric().isEmpty());
        server.verify();
    }

    @Test
    void shouldAddSongToOwnedPlaylist() {
        expectProfile();
        expectEditablePlaylist(101, 42, 9002);
        server.expect(request -> {
                    assertRequest(
                            request.getURI(),
                            "/playlist/tracks",
                            "op=add",
                            "pid=101",
                            "tracks=9001");
                    assertEquals(FORWARDED_COOKIE, request.getHeaders().getFirst(HttpHeaders.COOKIE));
                })
                .andRespond(withSuccess("{\"code\":200}", APPLICATION_JSON));

        PlaylistMutationData result = musicService.addSongToPlaylist(
                101,
                9001,
                loggedInRequest());

        assertEquals("added", result.status());
        server.verify();
    }

    @Test
    void shouldReturnDuplicateWithoutCallingMutationEndpoint() {
        expectProfile();
        expectEditablePlaylist(101, 42, 9001);

        PlaylistMutationData result = musicService.addSongToPlaylist(
                101,
                9001,
                loggedInRequest());

        assertEquals("duplicate", result.status());
        server.verify();
    }

    @Test
    void shouldRejectNestedMutationBodyWithoutRepeatingWrite() {
        expectProfile();
        expectEditablePlaylist(101, 42, 9002);
        server.expect(request -> assertRequest(
                        request.getURI(), "/playlist/tracks", "op=add", "pid=101", "tracks=9001"))
                .andRespond(withSuccess("{\"status\":200,\"body\":{\"code\":200}}", APPLICATION_JSON));

        NeteaseApiException error = assertThrows(NeteaseApiException.class,
                () -> musicService.addSongToPlaylist(101, 9001, loggedInRequest()));
        assertEquals("网易云 API 响应缺少有效字段: code", error.getMessage());
        server.verify();
    }

    @Test
    void shouldRejectPlaylistOwnedByAnotherUser() {
        expectProfile();
        expectEditablePlaylist(202, 99, 9002);

        PlaylistMutationData result = musicService.addSongToPlaylist(
                202,
                9001,
                loggedInRequest());

        assertEquals("forbidden", result.status());
        server.verify();
    }

    private void expectProfile() {
        server.expect(request -> assertRequest(request.getURI(), "/user/account"))
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "profile": {
                            "userId": 42,
                            "nickname": "Deep Tide",
                            "avatarUrl": "https://img.test/avatar.jpg"
                          }
                        }
                        """, APPLICATION_JSON));
    }

    private void expectSongDetail(long songId, String name, String artist, String album) {
        server.expect(request -> {
                    assertRequest(request.getURI(), "/song/detail", "ids=" + songId);
                    assertNull(request.getHeaders().getFirst(HttpHeaders.COOKIE));
                })
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "songs": [
                            {
                              "id": %d,
                              "name": "%s",
                              "ar": [{"id": 1, "name": "%s"}],
                              "al": {"id": 2, "name": "%s", "picUrl": ""},
                              "dt": 200000
                            }
                          ]
                        }
                        """.formatted(songId, name, artist, album), APPLICATION_JSON));
    }

    private void expectEditablePlaylist(long playlistId, long ownerId, long existingSongId) {
        server.expect(request -> assertRequest(
                        request.getURI(),
                        "/playlist/detail",
                        "id=" + playlistId))
                .andRespond(withSuccess("""
                        {
                          "code": 200,
                          "playlist": {
                            "id": %d,
                            "subscribed": false,
                            "creator": {"userId": %d},
                            "trackIds": [{"id": %d}]
                          }
                        }
                        """.formatted(playlistId, ownerId, existingSongId), APPLICATION_JSON));
    }

    private MockHttpServletRequest loggedInRequest() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.getSession(true).setAttribute(AuthService.NETEASE_COOKIE, COOKIE);
        return request;
    }

    private void assertRequest(URI uri, String expectedPath, String... expectedQueryParts) {
        assertEquals(expectedPath, uri.getPath());
        List<String> queryParts = Arrays.asList(uri.getQuery().split("&"));
        for (String expectedPart : expectedQueryParts) {
            assertTrue(queryParts.contains(expectedPart), "Missing query part: " + expectedPart);
        }
        assertTrue(queryParts.stream().anyMatch(value -> value.startsWith("timestamp=")));
    }
}
