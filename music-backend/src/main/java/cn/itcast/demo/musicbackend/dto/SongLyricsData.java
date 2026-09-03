package cn.itcast.demo.musicbackend.dto;

public record SongLyricsData(
        String lyric,
        String translation,
        String status) {
}
