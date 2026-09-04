package cn.itcast.demo.musicbackend.dto;

public record CurrentSongContextData(
        long songId,
        String name,
        String artist,
        String album,
        String originalLyrics,
        String translatedLyrics,
        LyricsStatus lyricsStatus) {
}
