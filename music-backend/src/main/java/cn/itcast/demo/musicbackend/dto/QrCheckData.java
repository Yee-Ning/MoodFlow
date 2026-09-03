package cn.itcast.demo.musicbackend.dto;

public record QrCheckData(
        int statusCode,
        String status,
        String statusMessage,
        boolean loggedIn) {
}
