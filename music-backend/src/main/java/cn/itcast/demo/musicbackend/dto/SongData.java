package cn.itcast.demo.musicbackend.dto;

import java.util.List;

public record SongData(
        long id,
        String name,
        List<String> artists,
        String album,
        String coverUrl,
        long duration) {
}
