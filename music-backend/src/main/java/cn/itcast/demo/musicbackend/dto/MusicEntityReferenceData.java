package cn.itcast.demo.musicbackend.dto;

public record MusicEntityReferenceData(
        long id,
        String entityType,
        String name,
        String artist,
        String album) {
}
