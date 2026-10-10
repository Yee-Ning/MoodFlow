package cn.itcast.demo.musicbackend.controller;

import java.util.List;

import cn.itcast.demo.musicbackend.common.ApiResponse;
import cn.itcast.demo.musicbackend.dto.CurrentSongContextData;
import cn.itcast.demo.musicbackend.dto.AlbumDetailData;
import cn.itcast.demo.musicbackend.dto.MusicEntityData;
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
import cn.itcast.demo.musicbackend.service.NeteaseMusicService;
import jakarta.servlet.http.HttpServletRequest;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api")
public class UserMusicController {

    private final NeteaseMusicService neteaseMusicService;

    public UserMusicController(NeteaseMusicService neteaseMusicService) {
        this.neteaseMusicService = neteaseMusicService;
    }

    @GetMapping("/user/profile")
    public ApiResponse<UserProfileData> getProfile(HttpServletRequest request) {
        return ApiResponse.success(neteaseMusicService.getCurrentUserProfile(request));
    }

    @GetMapping("/user/playlists")
    public ApiResponse<List<PlaylistData>> getPlaylists(HttpServletRequest request) {
        return ApiResponse.success(neteaseMusicService.getCurrentUserPlaylists(request));
    }

    @GetMapping("/music/search")
    public ApiResponse<List<SongData>> searchSongs(
            @RequestParam String keyword,
            @RequestParam(defaultValue = "10") int limit) {
        return ApiResponse.success(neteaseMusicService.searchSongs(keyword, limit));
    }

    @GetMapping("/music/entities/search")
    public ApiResponse<List<MusicEntityData>> searchMusicEntities(
            @RequestParam String keyword,
            @RequestParam(required = false) String type,
            @RequestParam(defaultValue = "5") int limit) {
        return ApiResponse.success(
                neteaseMusicService.searchMusicEntities(keyword, type, limit));
    }

    @GetMapping("/music/entities/{type}/{id}")
    public ApiResponse<MusicEntityData> getMusicEntity(
            @PathVariable String type,
            @PathVariable long id) {
        return ApiResponse.success(neteaseMusicService.getMusicEntityDetail(type, id));
    }

    @GetMapping("/songs/{id}/comment-summary")
    public ApiResponse<SongCommentSummaryData> getSongCommentSummary(@PathVariable long id) {
        return ApiResponse.success(neteaseMusicService.getSongCommentSummary(id));
    }

    @GetMapping("/songs/{id}")
    public ApiResponse<SongData> getSong(@PathVariable long id) {
        return ApiResponse.success(neteaseMusicService.getSongDetail(id));
    }

    @GetMapping("/albums/{id}")
    public ApiResponse<AlbumDetailData> getAlbum(@PathVariable long id) {
        return ApiResponse.success(neteaseMusicService.getAlbumDetail(id));
    }

    @GetMapping("/songs/{id}/context")
    public ApiResponse<CurrentSongContextData> getCurrentSongContext(@PathVariable long id) {
        return ApiResponse.success(neteaseMusicService.getCurrentSongContext(id));
    }

    @GetMapping("/playlists/{id}")
    public ApiResponse<PlaylistDetailData> getPlaylist(
            @PathVariable long id,
            HttpServletRequest request) {
        return ApiResponse.success(neteaseMusicService.getPlaylistDetail(id, request));
    }

    @GetMapping("/songs/{id}/url")
    public ApiResponse<SongPlaybackData> getSongPlaybackUrl(
            @PathVariable long id,
            HttpServletRequest request) {
        return ApiResponse.success(neteaseMusicService.getSongPlaybackUrl(id, request));
    }

    @GetMapping("/songs/{id}/lyrics")
    public ApiResponse<SongLyricsData> getSongLyrics(
            @PathVariable long id,
            HttpServletRequest request) {
        return ApiResponse.success(neteaseMusicService.getSongLyrics(id, request));
    }

    @PostMapping("/playlists/{playlistId}/songs/{songId}")
    public ApiResponse<PlaylistMutationData> addSongToPlaylist(
            @PathVariable long playlistId,
            @PathVariable long songId,
            HttpServletRequest request) {
        return ApiResponse.success(
                neteaseMusicService.addSongToPlaylist(playlistId, songId, request));
    }

    @PostMapping("/songs/{id}/like")
    public ApiResponse<SongLikeData> setSongLiked(
            @PathVariable long id, @RequestParam boolean liked, HttpServletRequest request) {
        return ApiResponse.success(neteaseMusicService.setSongLiked(id, liked, request));
    }

    @GetMapping("/user/liked-songs")
    public ApiResponse<List<Long>> getLikedSongs(HttpServletRequest request) {
        return ApiResponse.success(neteaseMusicService.getLikedSongIds(request));
    }

    @PostMapping("/playlists")
    public ApiResponse<CreatedPlaylistData> createPlaylist(
            @RequestParam String name,
            @RequestParam(defaultValue = "0") int privacy,
            HttpServletRequest request) {
        return ApiResponse.success(neteaseMusicService.createPlaylist(name, privacy, request));
    }

    @DeleteMapping("/playlists/{playlistId}/songs/{songId}")
    public ApiResponse<PlaylistMutationData> removeSongFromPlaylist(
            @PathVariable long playlistId, @PathVariable long songId, HttpServletRequest request) {
        return ApiResponse.success(neteaseMusicService.removeSongFromPlaylist(playlistId, songId, request));
    }

    @DeleteMapping("/playlists/{playlistId}")
    public ApiResponse<DeletedPlaylistData> deletePlaylist(
            @PathVariable long playlistId,
            @RequestParam(defaultValue = "false") boolean confirmed,
            HttpServletRequest request) {
        return ApiResponse.success(neteaseMusicService.deletePlaylist(playlistId, confirmed, request));
    }
}
