package cn.itcast.demo.musicbackend.dto;

public record SongLikeData(long songId, boolean liked, String status, String message) {
}
