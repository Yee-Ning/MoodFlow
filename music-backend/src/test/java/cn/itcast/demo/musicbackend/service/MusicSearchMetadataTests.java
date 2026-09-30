package cn.itcast.demo.musicbackend.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.http.MediaType.APPLICATION_JSON;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withServerError;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import java.util.stream.Collectors;
import java.util.stream.IntStream;

import cn.itcast.demo.musicbackend.controller.UserMusicController;
import cn.itcast.demo.musicbackend.exception.GlobalExceptionHandler;
import cn.itcast.demo.musicbackend.exception.NeteaseApiException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.client.RestClient;

class MusicSearchMetadataTests {
    private MockRestServiceServer server;
    private NeteaseMusicService service;
    private MockMvc mvc;

    @BeforeEach
    void setUp() {
        RestClient.Builder builder = RestClient.builder().baseUrl("http://localhost:3000");
        server = MockRestServiceServer.bindTo(builder).build();
        service = new NeteaseMusicService(builder.build());
        mvc = MockMvcBuilders.standaloneSetup(new UserMusicController(service))
                .setControllerAdvice(new GlobalExceptionHandler()).build();
    }

    private String song(long id, String name, String album) {
        return """
                {"id":%d,"name":"%s","ar":[{"id":37520,"name":"Kanye West"}],
                 "al":%s,"dt":180000}
                """.formatted(id, name, album);
    }

    private void expectSearch(String songs) {
        server.expect(request -> {
            assertEquals("/cloudsearch", request.getURI().getPath());
            assertNull(request.getHeaders().getFirst("Cookie"));
        }).andRespond(withSuccess("{\"code\":200,\"result\":{\"songs\":[" + songs + "]}}", APPLICATION_JSON));
    }

    @Test
    void shouldKeepAllFiftyResultsWhenFifteenthSongHasNoAlbum() throws Exception {
        String songs = IntStream.range(0, 50).mapToObj(index -> index == 14
                ? song(2148728095L, "EVERYBODY", "{\"id\":0,\"name\":\"\"}")
                : song(1000 + index, "Track " + index, "{\"id\":22,\"name\":\"Valid album\"}"))
                .collect(Collectors.joining(","));
        expectSearch(songs);
        expectSearch(songs);
        for (String keyword : new String[] { "kanye", "Kanye West" }) {
            mvc.perform(get("/api/music/entities/search").param("keyword", keyword)
                            .param("type", "song").param("limit", "50"))
                    .andExpect(status().isOk()).andExpect(jsonPath("$.data.length()").value(50))
                    .andExpect(jsonPath("$.data[14].id").value(2148728095L))
                    .andExpect(jsonPath("$.data[14].name").value("EVERYBODY"))
                    .andExpect(jsonPath("$.data[14].artist").value("Kanye West"))
                    .andExpect(jsonPath("$.data[14].album").value(""))
                    .andExpect(jsonPath("$.data[14].metadata.album_id").doesNotExist())
                    .andExpect(jsonPath("$.data[14].relatedEntities.length()").value(1))
                    .andExpect(jsonPath("$.data[0].relatedEntities[1].id").value(22));
        }
        server.verify();
    }

    @Test
    void shouldNeverGroundMissingOrUnidentifiedAlbums() {
        String[] albums = { "null", "{}", "{\"id\":22,\"name\":null}",
                "{\"id\":0,\"name\":\"Unreleased\"}", "{\"id\":-1,\"name\":\"Unreleased\"}" };
        for (String album : albums) {
            expectSearch(song(101, "EVERYBODY", album));
        }
        for (String album : albums) {
            var entity = service.searchMusicEntities("kanye", "song", 50).getFirst();
            assertEquals(101, entity.id());
            assertEquals(1, entity.relatedEntities().size());
            assertEquals("artist", entity.relatedEntities().getFirst().entityType());
            assertFalse(entity.metadata().containsKey("album_id"));
        }
        server.verify();
    }

    @Test
    void shouldKeepSongDetailAndAlbumTrackReferencesWithoutAlbumMetadata() {
        String track = song(101, "EVERYBODY", "null");
        server.expect(request -> assertEquals("/song/detail", request.getURI().getPath()))
                .andRespond(withSuccess("{\"code\":200,\"songs\":[" + track + "]}", APPLICATION_JSON));
        server.expect(request -> assertEquals("/album", request.getURI().getPath()))
                .andRespond(withSuccess("{\"code\":200,\"album\":{\"id\":22,\"name\":\"Valid album\"},\"songs\":["
                        + track + "]}", APPLICATION_JSON));
        assertEquals("", service.getSongDetail(101).album());
        var detail = service.getAlbumDetail(22);
        assertEquals(101, detail.songs().getFirst().id());
        assertEquals("", detail.album().relatedEntities().getFirst().album());
        server.verify();
    }

    @Test
    void shouldKeepNamedArtistsWithoutInventingUnknownArtistReferences() {
        expectSearch(song(101, "EVERYBODY", "{}").replace(
                "[{\"id\":37520,\"name\":\"Kanye West\"}]",
                "[{\"id\":37520,\"name\":\"Kanye West\"},{\"id\":0,\"name\":\"Guest\"},{\"id\":7,\"name\":null}]"));
        var entity = service.searchMusicEntities("kanye", "song", 50).getFirst();
        assertEquals("Kanye West、Guest", entity.artist());
        assertEquals(1, entity.relatedEntities().size());
        assertEquals(java.util.List.of(37520L), entity.metadata().get("artist_ids"));
        server.verify();
    }

    @Test
    void shouldStillRejectMissingSongIdentity() {
        expectSearch(song(101, "", "{}"));
        expectSearch(song(0, "EVERYBODY", "{}"));
        assertThrows(NeteaseApiException.class, () -> service.searchMusicEntities("kanye", "song", 50));
        assertThrows(NeteaseApiException.class, () -> service.searchMusicEntities("kanye", "song", 50));
        server.verify();
    }

    @Test
    void shouldKeepUpstreamFailureAndArgumentValidationContracts() throws Exception {
        mvc.perform(get("/api/music/entities/search").param("keyword", " ").param("type", "song"))
                .andExpect(status().isBadRequest());
        mvc.perform(get("/api/music/entities/search").param("keyword", "kanye").param("limit", "51"))
                .andExpect(status().isBadRequest());
        server.expect(request -> assertEquals("/cloudsearch", request.getURI().getPath())).andRespond(withServerError());
        mvc.perform(get("/api/music/entities/search").param("keyword", "kanye").param("type", "song"))
                .andExpect(status().isBadGateway()).andExpect(jsonPath("$.data").doesNotExist());
        server.verify();
    }
}
