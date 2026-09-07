package cn.itcast.demo.musicbackend.dto;

import java.util.List;
import java.util.Map;

public record MusicEntityData(
        long id,
        String entityType,
        String name,
        String artist,
        String album,
        String coverUrl,
        Map<String, Object> metadata,
        List<MusicEntityReferenceData> relatedEntities) {
}
