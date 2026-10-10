package cn.itcast.demo.musicbackend.service;

import java.net.SocketTimeoutException;
import java.net.http.HttpTimeoutException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.StringJoiner;

import cn.itcast.demo.musicbackend.dto.CurrentSongContextData;
import cn.itcast.demo.musicbackend.dto.AlbumDetailData;
import cn.itcast.demo.musicbackend.dto.LyricsStatus;
import cn.itcast.demo.musicbackend.dto.MusicEntityData;
import cn.itcast.demo.musicbackend.dto.MusicEntityReferenceData;
import cn.itcast.demo.musicbackend.dto.PlaylistData;
import cn.itcast.demo.musicbackend.dto.PlaylistDetailData;
import cn.itcast.demo.musicbackend.dto.PlaylistMutationData;
import cn.itcast.demo.musicbackend.dto.CreatedPlaylistData;
import cn.itcast.demo.musicbackend.dto.DeletedPlaylistData;
import cn.itcast.demo.musicbackend.dto.SongLikeData;
import cn.itcast.demo.musicbackend.dto.SongData;
import cn.itcast.demo.musicbackend.dto.SongCommentSummaryData;
import cn.itcast.demo.musicbackend.dto.SongLyricsData;
import cn.itcast.demo.musicbackend.dto.SongPlaybackData;
import cn.itcast.demo.musicbackend.dto.UserProfileData;
import cn.itcast.demo.musicbackend.exception.BadRequestException;
import cn.itcast.demo.musicbackend.exception.ForbiddenException;
import cn.itcast.demo.musicbackend.exception.UnprocessableRequestException;
import cn.itcast.demo.musicbackend.exception.NeteaseApiException;
import cn.itcast.demo.musicbackend.exception.NeteaseApiTimeoutException;
import cn.itcast.demo.musicbackend.exception.NotFoundException;
import cn.itcast.demo.musicbackend.exception.UnauthorizedException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpSession;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.stereotype.Service;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;

@Service
public class NeteaseMusicService {

    private static final Logger log = LoggerFactory.getLogger(NeteaseMusicService.class);
    private static final int PLAYLIST_LIMIT = 1000;
    private static final List<String> MUSIC_ENTITY_TYPES = List.of("song", "artist", "album", "playlist");

    private final RestClient neteaseRestClient;

    public NeteaseMusicService(RestClient neteaseRestClient) {
        this.neteaseRestClient = neteaseRestClient;
    }

    public UserProfileData getCurrentUserProfile(HttpServletRequest request) {
        return getProfile(requireCookie(request));
    }

    public List<PlaylistData> getCurrentUserPlaylists(HttpServletRequest request) {
        String cookie = requireCookie(request);
        UserProfileData profile = getProfile(cookie);
        return getUserPlaylists(cookie, profile.userId());
    }

    private List<PlaylistData> getUserPlaylists(String cookie, long userId) {
        List<PlaylistData> playlists = new ArrayList<>();
        int offset = 0;
        boolean hasMore;

        do {
            Map<String, Object> response = get("/user/playlist", Map.of(
                    "uid", userId,
                    "limit", PLAYLIST_LIMIT,
                    "offset", offset), cookie);
            List<Map<String, Object>> page = requiredList(response, "playlist");
            if (Boolean.TRUE.equals(response.get("more")) && page.isEmpty()) {
                throw new NeteaseApiException("网易云歌单分页不完整，无法确认完整自有歌单列表");
            }
            page.stream().map(this::toPlaylist).forEach(playlists::add);
            hasMore = Boolean.TRUE.equals(response.get("more")) && !page.isEmpty();
            offset += page.size();
        } while (hasMore);

        return List.copyOf(playlists);
    }

    public SongCommentSummaryData getSongCommentSummary(long songId) {
        if (songId <= 0) {
            throw new BadRequestException("歌曲 ID 必须为正整数");
        }
        // Gate 0 verified the actual top-level total. Never infer from page size.
        Map<String, Object> response = get("/comment/music", Map.of(
                "id", songId, "limit", 1, "offset", 0, "before", 0));
        Object total = response.get("total");
        if (!(total instanceof Integer || total instanceof Long)
                || ((Number) total).longValue() < 0) {
            throw invalidResponse("total");
        }
        return new SongCommentSummaryData(songId, ((Number) total).longValue());
    }

    public List<SongData> searchSongs(String keyword, int limit) {
        String normalizedKeyword = keyword == null ? "" : keyword.trim();
        if (normalizedKeyword.isBlank()) {
            throw new BadRequestException("搜索关键词不能为空");
        }
        if (limit < 1 || limit > 50) {
            throw new BadRequestException("limit 必须在 1 到 50 之间");
        }

        Map<String, Object> response = get("/cloudsearch", Map.of(
                "keywords", normalizedKeyword,
                "type", 1,
                "limit", limit));
        Map<String, Object> result = requiredMap(response, "result");
        if (!result.containsKey("songs") && optionalLong(result, "songCount") == 0) {
            return Collections.emptyList();
        }

        return requiredList(result, "songs").stream()
                .map(this::toSong)
                .toList();
    }

    public List<MusicEntityData> searchMusicEntities(
            String keyword,
            String entityType,
            int limit) {
        String normalizedKeyword = keyword == null ? "" : keyword.trim();
        if (normalizedKeyword.isBlank()) {
            throw new BadRequestException("搜索关键词不能为空");
        }
        if (limit < 1 || limit > 50) {
            throw new BadRequestException("limit 必须在 1 到 50 之间");
        }

        if (entityType != null && !entityType.isBlank()) {
            String normalizedType = entityType.trim().toLowerCase(Locale.ROOT);
            validateMusicEntityType(normalizedType);
            return searchMusicEntitiesByType(normalizedKeyword, normalizedType, limit);
        }

        List<MusicEntityData> entities = new ArrayList<>();
        for (String type : MUSIC_ENTITY_TYPES) {
            entities.addAll(searchMusicEntitiesByType(normalizedKeyword, type, limit));
        }
        return List.copyOf(entities);
    }

    public MusicEntityData getMusicEntityDetail(String entityType, long entityId) {
        if (entityId <= 0) {
            throw new BadRequestException("音乐实体 id 必须大于 0");
        }
        String normalizedType = entityType == null
                ? ""
                : entityType.trim().toLowerCase(Locale.ROOT);
        validateMusicEntityType(normalizedType);
        if ("playlist".equals(normalizedType)) {
            throw new BadRequestException("歌单详情请使用 /api/playlists/{id}");
        }

        return switch (normalizedType) {
            case "song" -> {
                MusicEntityData song = toSongEntity(fetchSongSource(entityId));
                List<MusicEntityReferenceData> related = new ArrayList<>(song.relatedEntities());
                try {
                    Map<String, Object> response = get("/simi/song", Map.of("id", entityId));
                    optionalMapList(response, "songs").stream()
                            .limit(5)
                            .map(this::toSongReference)
                            .forEach(related::add);
                } catch (NeteaseApiException | NeteaseApiTimeoutException exception) {
                    log.warn(
                            "相似歌曲暂时不可用，继续返回基础实体: songId={}, reason={}",
                            entityId,
                            exception.getMessage());
                }
                yield new MusicEntityData(
                        song.id(),
                        song.entityType(),
                        song.name(),
                        song.artist(),
                        song.album(),
                        song.coverUrl(),
                        song.metadata(),
                        List.copyOf(related));
            }
            case "artist" -> {
                Map<String, Object> response = get("/artists", Map.of("id", entityId));
                Map<String, Object> artist = requiredMap(response, "artist");
                List<MusicEntityReferenceData> related = optionalMapList(response, "hotSongs")
                        .stream()
                        .limit(8)
                        .map(this::toSongReference)
                        .toList();
                yield toArtistEntity(artist, related);
            }
            case "album" -> {
                Map<String, Object> response = get("/album", Map.of("id", entityId));
                Map<String, Object> album = requiredMap(response, "album");
                List<MusicEntityReferenceData> related = optionalMapList(response, "songs")
                        .stream()
                        .limit(8)
                        .map(this::toSongReference)
                        .toList();
                yield toAlbumEntity(album, related);
            }
            default -> throw new IllegalStateException("unreachable entity type");
        };
    }

    public AlbumDetailData getAlbumDetail(long albumId) {
        if (albumId <= 0) {
            throw new BadRequestException("专辑 id 必须大于 0");
        }
        Map<String, Object> response = get("/album", Map.of("id", albumId));
        Map<String, Object> albumSource = requiredMap(response, "album");
        List<Map<String, Object>> songSources = requiredList(response, "songs");
        MusicEntityData album = toAlbumEntity(
                albumSource,
                songSources.stream().map(this::toSongReference).toList());
        if (album.id() != albumId) {
            throw new NeteaseApiException("网易云 API 返回了不匹配的专辑数据");
        }
        return new AlbumDetailData(
                album,
                songSources.stream().map(this::toSong).toList());
    }

    public SongData getSongDetail(long songId) {
        if (songId <= 0) {
            throw new BadRequestException("歌曲 id 必须大于 0");
        }

        return toSong(fetchSongSource(songId));
    }

    private Map<String, Object> fetchSongSource(long songId) {
        Map<String, Object> response = get("/song/detail", Map.of("ids", songId));
        List<Map<String, Object>> songs = requiredList(response, "songs");
        if (songs.isEmpty()) {
            throw new NotFoundException("歌曲不存在");
        }
        return songs.getFirst();
    }

    public CurrentSongContextData getCurrentSongContext(long songId) {
        SongData song = getSongDetail(songId);

        try {
            SongLyricsData lyrics = fetchSongLyrics(songId, null);
            LyricsStatus lyricsStatus = "available".equals(lyrics.status())
                    ? LyricsStatus.AVAILABLE
                    : LyricsStatus.NO_LYRICS;
            return toCurrentSongContext(song, lyrics, lyricsStatus);
        } catch (NeteaseApiException | NeteaseApiTimeoutException exception) {
            log.warn("当前歌曲歌词暂时不可用: songId={}, reason={}", songId, exception.getMessage());
            return toCurrentSongContext(
                    song,
                    new SongLyricsData("", "", "unavailable"),
                    LyricsStatus.UNAVAILABLE);
        }
    }

    public PlaylistDetailData getPlaylistDetail(long playlistId, HttpServletRequest request) {
        if (playlistId <= 0) {
            throw new BadRequestException("歌单 id 必须大于 0");
        }

        String cookie = requireCookie(request);
        Map<String, Object> detailResponse = get(
                "/playlist/detail",
                Map.of("id", playlistId),
                cookie);
        Map<String, Object> playlistSource = requiredMap(detailResponse, "playlist");
        PlaylistData playlist = toPlaylist(playlistSource);

        List<SongData> songs = getAllSongs(playlistId, playlist.trackCount(), cookie);

        return new PlaylistDetailData(
                playlist,
                optionalString(playlistSource, "description"),
                songs);
    }

    public SongPlaybackData getSongPlaybackUrl(long songId, HttpServletRequest request) {
        if (songId <= 0) {
            throw new BadRequestException("歌曲 id 必须大于 0");
        }

        String cookie = requireCookie(request);
        Map<String, Object> response = get(
                "/song/url/v1",
                Map.of("id", songId, "level", "standard"),
                cookie);
        List<Map<String, Object>> data = requiredList(response, "data");

        if (data.isEmpty()) {
            return unavailablePlayback(songId);
        }

        Map<String, Object> source = data.getFirst();
        String url = optionalString(source, "url");
        if (url.isBlank()) {
            return unavailablePlayback(songId);
        }

        return new SongPlaybackData(songId, url, true, "");
    }

    public SongLyricsData getSongLyrics(long songId, HttpServletRequest request) {
        if (songId <= 0) {
            throw new BadRequestException("歌曲 id 必须大于 0");
        }

        return fetchSongLyrics(songId, requireCookie(request));
    }

    private SongLyricsData fetchSongLyrics(long songId, String cookie) {
        Map<String, Object> response = get(
                "/lyric",
                Map.of("id", songId),
                cookie);

        if (Boolean.TRUE.equals(response.get("nolyric"))) {
            return new SongLyricsData("", "", "instrumental");
        }

        Map<String, Object> lyricSource = optionalMap(response, "lrc");
        String lyric = optionalString(lyricSource, "lyric");
        if (Boolean.TRUE.equals(response.get("uncollected")) || lyric.isBlank()) {
            return new SongLyricsData("", "", "unavailable");
        }

        String translation = optionalString(optionalMap(response, "tlyric"), "lyric");
        return new SongLyricsData(lyric, translation, "available");
    }

    private CurrentSongContextData toCurrentSongContext(
            SongData song,
            SongLyricsData lyrics,
            LyricsStatus lyricsStatus) {
        return new CurrentSongContextData(
                song.id(),
                song.name(),
                String.join("、", song.artists()),
                song.album(),
                lyrics.lyric(),
                lyrics.translation(),
                lyricsStatus);
    }

    public PlaylistMutationData addSongToPlaylist(
            long playlistId,
            long songId,
            HttpServletRequest request) {
        if (playlistId <= 0) {
            throw new BadRequestException("歌单 id 必须大于 0");
        }
        if (songId <= 0) {
            throw new BadRequestException("歌曲 id 必须大于 0");
        }

        String cookie = requireCookie(request);
        UserProfileData profile = getProfile(cookie);
        Map<String, Object> detailResponse = get(
                "/playlist/detail",
                Map.of("id", playlistId),
                cookie);
        Map<String, Object> playlist = requiredMap(detailResponse, "playlist");
        Map<String, Object> creator = requiredMap(playlist, "creator");
        long creatorId = requiredLong(creator, "userId");

        if (creatorId != profile.userId() || Boolean.TRUE.equals(playlist.get("subscribed"))) {
            return new PlaylistMutationData("forbidden", "该歌单不可编辑");
        }

        if (playlistContainsSong(playlist, songId)) {
            return new PlaylistMutationData("duplicate", "歌曲已在该歌单中");
        }

        get(
                "/playlist/tracks",
                Map.of("op", "add", "pid", playlistId, "tracks", songId),
                cookie);
        return new PlaylistMutationData("added", "已添加到歌单");
    }

    public SongLikeData setSongLiked(long songId, boolean liked, HttpServletRequest request) {
        if (songId <= 0) {
            throw new BadRequestException("歌曲 id 必须大于 0");
        }
        String cookie = requireCookie(request);
        post("/like", Map.of("id", songId, "like", liked), cookie);
        return new SongLikeData(songId, liked, "synced",
                liked ? "已同步到网易云我喜欢的音乐" : "已取消网易云红心收藏");
    }

    public List<Long> getLikedSongIds(HttpServletRequest request) {
        String cookie = requireCookie(request);
        UserProfileData profile = getProfile(cookie);
        Map<String, Object> response = get("/likelist", Map.of("uid", profile.userId()), cookie);
        if (!(response.get("ids") instanceof List<?> ids)) {
            throw invalidResponse("ids");
        }
        List<Long> result = new ArrayList<>();
        for (Object id : ids) {
            if (!(id instanceof Number number) || number.longValue() <= 0) {
                throw invalidResponse("ids");
            }
            result.add(number.longValue());
        }
        return List.copyOf(result);
    }

    public CreatedPlaylistData createPlaylist(String name, int privacy, HttpServletRequest request) {
        String normalizedName = name == null ? "" : name.trim();
        if (normalizedName.isBlank() || normalizedName.length() > 40) {
            throw new BadRequestException("歌单名称必须为 1 到 40 个字符");
        }
        if (privacy != 0 && privacy != 10) {
            throw new BadRequestException("privacy 必须是 0（公开）或 10（私密）");
        }
        String cookie = requireCookie(request);
        Map<String, Object> response = post("/playlist/create",
                Map.of("name", normalizedName, "privacy", privacy, "type", "NORMAL"), cookie);
        Map<String, Object> playlist = requiredMap(response, "playlist");
        long playlistId = requiredLong(playlist, "id");
        String createdName = requiredString(playlist, "name");
        if (playlistId <= 0 || !createdName.equals(normalizedName)) {
            throw new NeteaseApiException("网易云 API 返回了不匹配的新建歌单数据");
        }
        return new CreatedPlaylistData(playlistId, createdName, "created", "已新建歌单");
    }

    public PlaylistMutationData removeSongFromPlaylist(
            long playlistId, long songId, HttpServletRequest request) {
        if (playlistId <= 0 || songId <= 0) {
            throw new BadRequestException("歌单和歌曲 id 必须大于 0");
        }
        String cookie = requireCookie(request);
        UserProfileData profile = getProfile(cookie);
        Map<String, Object> response = get("/playlist/detail", Map.of("id", playlistId), cookie);
        Map<String, Object> playlist = requiredMap(response, "playlist");
        if (requiredLong(playlist, "id") != playlistId) {
            throw new NeteaseApiException("网易云 API 返回了不匹配的歌单数据");
        }
        long creatorId = requiredLong(requiredMap(playlist, "creator"), "userId");
        if (creatorId != profile.userId() || Boolean.TRUE.equals(playlist.get("subscribed"))) {
            return new PlaylistMutationData("forbidden", "该歌单不可编辑");
        }
        // A missing track list cannot prove that a destructive operation is safe.
        requiredList(playlist, "trackIds");
        if (!playlistContainsSong(playlist, songId)) {
            return new PlaylistMutationData("not_found", "歌曲不在该歌单中，无需移除");
        }
        post("/playlist/tracks", Map.of("op", "del", "pid", playlistId, "tracks", songId), cookie);
        return new PlaylistMutationData("removed", "已从歌单移除歌曲");
    }

    public DeletedPlaylistData deletePlaylist(long playlistId, boolean confirmed, HttpServletRequest request) {
        if (playlistId <= 0) {
            throw new BadRequestException("歌单 id 必须大于 0");
        }
        if (!confirmed) {
            throw new UnprocessableRequestException("删除整张歌单需要用户确认准确歌单 ID");
        }
        String cookie = requireCookie(request);
        UserProfileData profile = getProfile(cookie);
        Map<String, Object> response = get("/playlist/detail", Map.of("id", playlistId), cookie);
        Object raw = response.get("playlist");
        if (!(raw instanceof Map<?, ?> playlist)
                || !(playlist.get("id") instanceof Number id) || id.longValue() != playlistId
                || id.doubleValue() != id.longValue()
                || !(playlist.get("creator") instanceof Map<?, ?> creator)
                || !(creator.get("userId") instanceof Number owner) || owner.longValue() <= 0
                || owner.doubleValue() != owner.longValue()
                || !(playlist.get("subscribed") instanceof Boolean)
                || !(playlist.get("specialType") instanceof Number specialType)) {
            throw new UnprocessableRequestException("歌单身份、创建者或自建类型不明，拒绝删除");
        }
        if (owner.longValue() != profile.userId() || Boolean.TRUE.equals(playlist.get("subscribed"))) {
            throw new ForbiddenException("仅允许删除当前账号自建歌单");
        }
        if (specialType.doubleValue() != 0) {
            throw new ForbiddenException("系统歌单（含我喜欢的音乐）不可删除");
        }
        // Dispatch once. Any exception after this point is an uncertain effect;
        // the AI operation journal must reconcile with reads, never repeat it.
        post("/playlist/delete", Map.of("id", playlistId), cookie);
        try {
            if (getProfile(cookie).userId() != profile.userId()) {
                throw new NeteaseApiException("删除后读回账号身份不一致；效果未知");
            }
            if (getUserPlaylists(cookie, profile.userId()).stream().anyMatch(row -> row.id() == playlistId)) {
                throw new NeteaseApiException("删除请求已提交，但自有歌单读回仍包含目标；效果未知，禁止重复删除");
            }
        } catch (UnauthorizedException exception) {
            // A post-dispatch read rejection cannot prove that the write failed.
            throw new NeteaseApiException("删除请求已提交，但读回身份失效；效果未知：" + exception.getMessage(), exception);
        }
        return new DeletedPlaylistData(playlistId, "deleted", "自有歌单列表读回已确认目标歌单消失");
    }

    private SongPlaybackData unavailablePlayback(long songId) {
        return new SongPlaybackData(
                songId,
                "",
                false,
                "该歌曲可能因版权、会员或地区限制暂时无法播放");
    }

    private boolean playlistContainsSong(Map<String, Object> playlist, long songId) {
        Object value = playlist.get("trackIds");
        if (!(value instanceof List<?> trackIds)) {
            return false;
        }

        for (Object item : trackIds) {
            if (item instanceof Map<?, ?> track
                    && track.get("id") instanceof Number id
                    && id.longValue() == songId) {
                return true;
            }
        }
        return false;
    }

    private UserProfileData getProfile(String cookie) {
        Map<String, Object> response = get("/user/account", Map.of(), cookie);
        Object profileValue = response.get("profile");
        if (!(profileValue instanceof Map<?, ?>)) {
            throw new UnauthorizedException("网易云登录已失效，请重新扫码登录");
        }
        Map<String, Object> profile = requiredMap(response, "profile");
        return new UserProfileData(
                requiredLong(profile, "userId"),
                requiredString(profile, "nickname"),
                optionalString(profile, "avatarUrl"));
    }

    private List<SongData> getAllSongs(long playlistId, int trackCount, String cookie) {
        if (trackCount <= 0) {
            return Collections.emptyList();
        }

        List<SongData> songs = new ArrayList<>(trackCount);
        int offset = 0;
        while (offset < trackCount) {
            int limit = Math.min(PLAYLIST_LIMIT, trackCount - offset);
            Map<String, Object> tracksResponse = get(
                    "/playlist/track/all",
                    Map.of("id", playlistId, "limit", limit, "offset", offset),
                    cookie);
            List<Map<String, Object>> page = requiredList(tracksResponse, "songs");
            page.stream().map(this::toSong).forEach(songs::add);
            if (page.size() < limit) {
                break;
            }
            offset += page.size();
        }
        return List.copyOf(songs);
    }

    private PlaylistData toPlaylist(Map<String, Object> source) {
        Map<String, Object> creator = requiredMap(source, "creator");
        return new PlaylistData(
                requiredLong(source, "id"),
                requiredString(source, "name"),
                optionalString(source, "coverImgUrl"),
                optionalInt(source, "trackCount"),
                requiredString(creator, "nickname"),
                Boolean.TRUE.equals(source.get("subscribed")),
                creator.get("userId") instanceof Number id && id.longValue() > 0 ? id.longValue() : null,
                source.get("specialType") instanceof Number type ? type.intValue() : null);
    }

    private SongData toSong(Map<String, Object> source) {
        List<String> artists = artistNames(requiredList(source, "ar"));
        Map<String, Object> album = optionalMap(source, "al");

        return new SongData(
                requiredEntityId(source),
                requiredString(source, "name"),
                artists,
                optionalString(album, "name"),
                optionalString(album, "picUrl"),
                optionalLong(source, "dt"));
    }

    private List<MusicEntityData> searchMusicEntitiesByType(
            String keyword,
            String entityType,
            int limit) {
        int searchType = switch (entityType) {
            case "song" -> 1;
            case "album" -> 10;
            case "artist" -> 100;
            case "playlist" -> 1000;
            default -> throw new IllegalStateException("unreachable entity type");
        };
        String resultField = switch (entityType) {
            case "song" -> "songs";
            case "album" -> "albums";
            case "artist" -> "artists";
            case "playlist" -> "playlists";
            default -> throw new IllegalStateException("unreachable entity type");
        };
        Map<String, Object> response = get("/cloudsearch", Map.of(
                "keywords", keyword,
                "type", searchType,
                "limit", limit));
        Map<String, Object> result = requiredMap(response, "result");
        List<Map<String, Object>> items = optionalMapList(result, resultField);
        return switch (entityType) {
            case "song" -> items.stream().map(this::toSongEntity).toList();
            case "album" -> items.stream().map(item -> toAlbumEntity(item, List.of())).toList();
            case "artist" -> items.stream().map(item -> toArtistEntity(item, List.of())).toList();
            case "playlist" -> items.stream().map(this::toPlaylistEntity).toList();
            default -> throw new IllegalStateException("unreachable entity type");
        };
    }

    private MusicEntityData toPlaylistEntity(Map<String, Object> source) {
        Map<String, Object> creator = requiredMap(source, "creator");
        return new MusicEntityData(
                requiredLong(source, "id"),
                "playlist",
                requiredString(source, "name"),
                optionalString(creator, "nickname"),
                "",
                optionalString(source, "coverImgUrl"),
                Map.of("track_count", optionalInt(source, "trackCount")),
                List.of());
    }

    private MusicEntityData toSongEntity(Map<String, Object> source) {
        List<Map<String, Object>> artistSources = requiredList(source, "ar");
        List<String> artists = artistNames(artistSources);
        Map<String, Object> album = optionalMap(source, "al");
        String albumName = optionalString(album, "name");
        long albumId = optionalLong(album, "id");

        List<MusicEntityReferenceData> artistReferences = artistReferences(artistSources);
        List<MusicEntityReferenceData> related = new ArrayList<>(artistReferences);

        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("duration", optionalLong(source, "dt"));
        metadata.put("artist_ids", artistReferences.stream()
                .map(MusicEntityReferenceData::id)
                .toList());
        // Some real songs have an unreleased/unknown album (id=0, name="").
        // Keep the song; expose a link only when the provider identifies the album.
        if (albumId > 0 && !albumName.isBlank()) {
            related.add(new MusicEntityReferenceData(
                    albumId, "album", albumName, String.join("、", artists), ""));
            metadata.put("album_id", albumId);
        }
        return new MusicEntityData(
                requiredEntityId(source),
                "song",
                requiredString(source, "name"),
                String.join("、", artists),
                albumName,
                optionalString(album, "picUrl"),
                Map.copyOf(metadata),
                List.copyOf(related));
    }

    private MusicEntityData toArtistEntity(
            Map<String, Object> source,
            List<MusicEntityReferenceData> related) {
        String coverUrl = optionalString(source, "picUrl");
        if (coverUrl.isBlank()) {
            coverUrl = optionalString(source, "img1v1Url");
        }
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("aliases", optionalStringList(source, "alias"));
        metadata.put("album_size", optionalLong(source, "albumSize"));
        metadata.put("music_size", optionalLong(source, "musicSize"));
        metadata.put("description", optionalString(source, "briefDesc"));
        return new MusicEntityData(
                requiredLong(source, "id"),
                "artist",
                requiredString(source, "name"),
                requiredString(source, "name"),
                "",
                coverUrl,
                Map.copyOf(metadata),
                List.copyOf(related));
    }

    private MusicEntityData toAlbumEntity(
            Map<String, Object> source,
            List<MusicEntityReferenceData> relatedSongs) {
        List<Map<String, Object>> artistSources = optionalMapList(source, "artists");
        if (artistSources.isEmpty() && source.get("artist") instanceof Map<?, ?>) {
            artistSources = List.of(requiredMap(source, "artist"));
        }
        List<String> artists = artistNames(artistSources);
        List<MusicEntityReferenceData> artistReferences = artistReferences(artistSources);
        List<MusicEntityReferenceData> related = new ArrayList<>(artistReferences);
        related.addAll(relatedSongs);

        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("publish_time", optionalLong(source, "publishTime"));
        metadata.put("company", optionalString(source, "company"));
        metadata.put("description", optionalString(source, "description"));
        metadata.put("size", optionalLong(source, "size"));
        metadata.put("artist_ids", artistReferences.stream()
                .map(MusicEntityReferenceData::id)
                .toList());
        return new MusicEntityData(
                requiredLong(source, "id"),
                "album",
                requiredString(source, "name"),
                String.join("、", artists),
                requiredString(source, "name"),
                optionalString(source, "picUrl"),
                Map.copyOf(metadata),
                List.copyOf(related));
    }

    private MusicEntityReferenceData toArtistReference(Map<String, Object> source) {
        String name = requiredString(source, "name");
        return new MusicEntityReferenceData(
                requiredLong(source, "id"),
                "artist",
                name,
                name,
                "");
    }

    private MusicEntityReferenceData toSongReference(Map<String, Object> source) {
        List<String> artists = artistNames(requiredList(source, "ar"));
        Map<String, Object> album = optionalMap(source, "al");
        return new MusicEntityReferenceData(
                requiredEntityId(source),
                "song",
                requiredString(source, "name"),
                String.join("、", artists),
                optionalString(album, "name"));
    }

    private List<String> artistNames(List<Map<String, Object>> sources) {
        return sources.stream().map(source -> optionalString(source, "name"))
                .filter(name -> !name.isBlank()).toList();
    }

    private List<MusicEntityReferenceData> artistReferences(List<Map<String, Object>> sources) {
        return sources.stream()
                .filter(source -> optionalLong(source, "id") > 0 && !optionalString(source, "name").isBlank())
                .map(this::toArtistReference).toList();
    }

    private long requiredEntityId(Map<String, Object> source) {
        long id = requiredLong(source, "id");
        if (id <= 0) throw invalidResponse("id");
        return id;
    }

    private void validateMusicEntityType(String entityType) {
        if (!MUSIC_ENTITY_TYPES.contains(entityType)) {
            throw new BadRequestException("type 必须是 song、artist、album 或 playlist");
        }
    }

    private String requireCookie(HttpServletRequest request) {
        HttpSession session = request.getSession(false);
        Object value = session == null ? null : session.getAttribute(AuthService.NETEASE_COOKIE);
        if (value instanceof String cookie && !cookie.isBlank()) {
            return cookie;
        }
        throw new UnauthorizedException("请先登录网易云音乐");
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> get(
            String path,
            Map<String, Object> queryParams) {
        return get(path, queryParams, null);
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> get(
            String path,
            Map<String, Object> queryParams,
            String cookie) {
        return request(HttpMethod.GET, path, queryParams, cookie);
    }

    private Map<String, Object> post(String path, Map<String, Object> queryParams, String cookie) {
        return request(HttpMethod.POST, path, queryParams, cookie);
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> request(
            HttpMethod method, String path, Map<String, Object> queryParams, String cookie) {
        try {
            var requestSpec = neteaseRestClient.method(method)
                    .uri(uriBuilder -> {
                        uriBuilder.path(path);
                        queryParams.forEach(uriBuilder::queryParam);
                        return uriBuilder
                                .queryParam("timestamp", System.currentTimeMillis())
                                .build();
                    });
            if (cookie != null && !cookie.isBlank()) {
                requestSpec.header(HttpHeaders.COOKIE, normalizeCookieHeader(cookie));
            }

            Map<String, Object> response = requestSpec.retrieve()
                    .body(Map.class);
            if (response == null) {
                throw new NeteaseApiException("网易云 API 返回空响应");
            }
            validateResponseCode(response);
            return response;
        } catch (ResourceAccessException exception) {
            if (hasCause(exception, SocketTimeoutException.class)
                    || hasCause(exception, HttpTimeoutException.class)) {
                throw new NeteaseApiTimeoutException("网易云 API 请求超时", exception);
            }
            throw new NeteaseApiException("无法连接网易云 API", exception);
        } catch (RestClientResponseException exception) {
            if (exception.getStatusCode().value() == 401) {
                throw new UnauthorizedException("网易云登录已失效，请重新扫码登录");
            }
            throw new NeteaseApiException("网易云 API 响应异常", exception);
        } catch (RestClientException exception) {
            throw new NeteaseApiException("网易云 API 响应异常", exception);
        }
    }

    private void validateResponseCode(Map<String, Object> response) {
        Object value = response.get("code");
        if (!(value instanceof Number number)) {
            throw invalidResponse("code");
        }
        if (number.intValue() == 301) {
            throw new UnauthorizedException("网易云登录已失效，请重新扫码登录");
        }
        if (number.intValue() != 200) {
            String message = optionalString(response, "message");
            if (message.isBlank()) {
                message = optionalString(response, "msg");
            }
            throw new NeteaseApiException(message.isBlank()
                    ? "网易云 API 请求失败，状态码: " + number.intValue()
                    : message);
        }
    }

    private String normalizeCookieHeader(String rawCookie) {
        StringJoiner normalized = new StringJoiner("; ");
        for (String segment : rawCookie.split(";")) {
            String item = segment.trim();
            int separator = item.indexOf('=');
            if (separator <= 0) {
                continue;
            }

            String name = item.substring(0, separator).trim();
            if (isCookieAttribute(name)) {
                continue;
            }
            normalized.add(name + "=" + item.substring(separator + 1).trim());
        }

        String result = normalized.toString();
        if (result.isBlank()) {
            throw new UnauthorizedException("网易云登录 Cookie 无效，请重新扫码登录");
        }
        return result;
    }

    private boolean isCookieAttribute(String name) {
        return switch (name.toLowerCase(Locale.ROOT)) {
            case "path", "max-age", "expires", "domain", "samesite", "priority" -> true;
            default -> false;
        };
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> requiredMap(Map<String, Object> source, String name) {
        Object value = source.get(name);
        if (value instanceof Map<?, ?> map) {
            return (Map<String, Object>) map;
        }
        throw invalidResponse(name);
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> optionalMap(Map<String, Object> source, String name) {
        Object value = source.get(name);
        if (value instanceof Map<?, ?> map) {
            return (Map<String, Object>) map;
        }
        return Collections.emptyMap();
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> requiredList(Map<String, Object> source, String name) {
        Object value = source.get(name);
        if (!(value instanceof List<?> list)) {
            throw invalidResponse(name);
        }
        if (list.isEmpty()) {
            return Collections.emptyList();
        }

        List<Map<String, Object>> result = new ArrayList<>(list.size());
        for (Object item : list) {
            if (!(item instanceof Map<?, ?> map)) {
                throw invalidResponse(name);
            }
            result.add((Map<String, Object>) map);
        }
        return result;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> optionalMapList(Map<String, Object> source, String name) {
        Object value = source.get(name);
        if (!(value instanceof List<?> list)) {
            return Collections.emptyList();
        }
        List<Map<String, Object>> result = new ArrayList<>(list.size());
        for (Object item : list) {
            if (!(item instanceof Map<?, ?> map)) {
                throw invalidResponse(name);
            }
            result.add((Map<String, Object>) map);
        }
        return result;
    }

    private List<String> optionalStringList(Map<String, Object> source, String name) {
        Object value = source.get(name);
        if (!(value instanceof List<?> list)) {
            return Collections.emptyList();
        }
        List<String> result = new ArrayList<>(list.size());
        for (Object item : list) {
            if (item instanceof String stringValue && !stringValue.isBlank()) {
                result.add(stringValue);
            }
        }
        return List.copyOf(result);
    }

    private String requiredString(Map<String, Object> source, String name) {
        String value = optionalString(source, name);
        if (!value.isBlank()) {
            return value;
        }
        throw invalidResponse(name);
    }

    private String optionalString(Map<String, Object> source, String name) {
        Object value = source.get(name);
        return value instanceof String stringValue ? stringValue : "";
    }

    private long requiredLong(Map<String, Object> source, String name) {
        Object value = source.get(name);
        if (value instanceof Number number) {
            return number.longValue();
        }
        throw invalidResponse(name);
    }

    private long optionalLong(Map<String, Object> source, String name) {
        Object value = source.get(name);
        return value instanceof Number number ? number.longValue() : 0;
    }

    private int optionalInt(Map<String, Object> source, String name) {
        Object value = source.get(name);
        return value instanceof Number number ? number.intValue() : 0;
    }

    private NeteaseApiException invalidResponse(String fieldName) {
        return new NeteaseApiException("网易云 API 响应缺少有效字段: " + fieldName);
    }

    private boolean hasCause(Throwable throwable, Class<? extends Throwable> causeType) {
        Throwable current = throwable;
        while (current != null) {
            if (causeType.isInstance(current)) {
                return true;
            }
            current = current.getCause();
        }
        return false;
    }
}
