package cn.itcast.demo.musicbackend.dto;

public record PlaylistData(
        long id,
        String name,
        String coverImgUrl,
        int trackCount,
        String creator,
        boolean subscribed) {
}
