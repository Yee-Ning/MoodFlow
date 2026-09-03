package cn.itcast.demo.musicbackend.dto;

import java.util.List;

public record PlaylistDetailData(
        PlaylistData playlist,
        String description,
        List<SongData> songs) {
}
