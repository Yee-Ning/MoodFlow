package cn.itcast.demo.musicbackend.dto;

public record SongPlaybackData(
        long songId,
        String url,
        boolean available,
        String message) {
}
