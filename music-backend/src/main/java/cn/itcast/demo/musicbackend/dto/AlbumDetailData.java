package cn.itcast.demo.musicbackend.dto;

import java.util.List;

public record AlbumDetailData(
        MusicEntityData album,
        List<SongData> songs) {
}
