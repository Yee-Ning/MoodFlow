package cn.itcast.demo.musicbackend.controller;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.springframework.http.MediaType.APPLICATION_JSON;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withServerError;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.Arrays;

import cn.itcast.demo.musicbackend.exception.GlobalExceptionHandler;
import cn.itcast.demo.musicbackend.service.NeteaseMusicService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.client.RestClient;

class MusicAssetControllerTests {
    private MockRestServiceServer server;
    private MockMvc mockMvc;
    private MockHttpSession session;

    @BeforeEach
    void setUp() {
        RestClient.Builder builder = RestClient.builder().baseUrl("http://localhost:3000");
        server = MockRestServiceServer.bindTo(builder).build();
        mockMvc = MockMvcBuilders.standaloneSetup(new UserMusicController(new NeteaseMusicService(builder.build())))
                .setControllerAdvice(new GlobalExceptionHandler()).build();
        session = new MockHttpSession();
        session.setAttribute("NETEASE_COOKIE", "MUSIC_U=fake-session; Path=/;");
    }

    private void expect(String path, HttpMethod method, String body, String... params) {
        server.expect(request -> {
            assertEquals(path, request.getURI().getPath());
            assertEquals(method, request.getMethod());
            assertEquals("MUSIC_U=fake-session", request.getHeaders().getFirst(HttpHeaders.COOKIE));
            var query = Arrays.asList(request.getURI().getQuery().split("&"));
            for (String param : params) org.junit.jupiter.api.Assertions.assertTrue(query.contains(param));
        }).andRespond(withSuccess(body, APPLICATION_JSON));
    }

    private void expectProfile() {
        expect("/user/account", HttpMethod.GET,
                "{\"code\":200,\"profile\":{\"userId\":42,\"nickname\":\"Test User\"}}");
    }

    private void expectPlaylist(long creatorId, boolean subscribed, long songId) {
        expect("/playlist/detail", HttpMethod.GET, """
                {"code":200,"playlist":{"id":77,"creator":{"userId":%d},
                "subscribed":%s,"trackIds":[{"id":%d}]}}
                """.formatted(creatorId, subscribed, songId), "id=77");
    }

    @Test
    void shouldLikeAndUnlikeWithAuthenticatedExplicitState() throws Exception {
        expect("/like", HttpMethod.POST, "{\"code\":200}", "id=101", "like=true");
        expect("/like", HttpMethod.POST, "{\"code\":200}", "id=101", "like=false");
        mockMvc.perform(post("/api/songs/101/like").param("liked", "true").session(session))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data.songId").value(101))
                .andExpect(jsonPath("$.data.liked").value(true)).andExpect(jsonPath("$.data.status").value("synced"));
        mockMvc.perform(post("/api/songs/101/like").param("liked", "false").session(session))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data.liked").value(false));
        server.verify();
    }

    @Test
    void shouldLoadCloudLikesForCurrentUser() throws Exception {
        expectProfile();
        expect("/likelist", HttpMethod.GET, "{\"code\":200,\"ids\":[101,102]}", "uid=42");
        mockMvc.perform(get("/api/user/liked-songs").session(session))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data[0]").value(101));
        server.verify();
    }

    @Test
    void shouldCreatePrivateNormalPlaylistAndReturnGroundedId() throws Exception {
        expect("/playlist/create", HttpMethod.POST,
                "{\"code\":200,\"playlist\":{\"id\":77,\"name\":\"Study\"}}",
                "name=Study", "privacy=10", "type=NORMAL");
        mockMvc.perform(post("/api/playlists").param("name", " Study ").param("privacy", "10").session(session))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data.playlistId").value(77))
                .andExpect(jsonPath("$.data.name").value("Study"))
                .andExpect(jsonPath("$.data.status").value("created"));
        server.verify();
    }

    @Test
    void shouldRemoveSongOnlyAfterCheckingOwnershipAndMembership() throws Exception {
        expectProfile();
        expectPlaylist(42, false, 101);
        expect("/playlist/tracks", HttpMethod.POST, "{\"code\":200}", "op=del", "pid=77", "tracks=101");
        mockMvc.perform(delete("/api/playlists/77/songs/101").session(session))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data.status").value("removed"));
        server.verify();
    }

    @Test
    void shouldRejectForeignAndSubscribedPlaylistsWithoutMutation() throws Exception {
        expectProfile();
        expectPlaylist(99, false, 101);
        expectProfile();
        expectPlaylist(42, true, 101);
        for (int index = 0; index < 2; index++) {
            mockMvc.perform(delete("/api/playlists/77/songs/101").session(session))
                    .andExpect(status().isOk()).andExpect(jsonPath("$.data.status").value("forbidden"));
        }
        server.verify();
    }

    @Test
    void shouldTreatAlreadyRemovedSongAsNoOp() throws Exception {
        expectProfile();
        expectPlaylist(42, false, 102);
        mockMvc.perform(delete("/api/playlists/77/songs/101").session(session))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data.status").value("not_found"));
        server.verify();
    }

    @Test
    void shouldRejectMissingSessionForAllAssetEndpoints() throws Exception {
        mockMvc.perform(post("/api/songs/101/like").param("liked", "true")).andExpect(status().isUnauthorized());
        mockMvc.perform(post("/api/playlists").param("name", "Study")).andExpect(status().isUnauthorized());
        mockMvc.perform(delete("/api/playlists/77/songs/101")).andExpect(status().isUnauthorized());
        mockMvc.perform(get("/api/user/liked-songs")).andExpect(status().isUnauthorized());
        server.verify();
    }

    @Test
    void shouldRejectInvalidArgumentsBeforeNetworkCall() throws Exception {
        mockMvc.perform(post("/api/songs/0/like").param("liked", "true").session(session)).andExpect(status().isBadRequest());
        mockMvc.perform(post("/api/songs/101/like").param("liked", "maybe").session(session)).andExpect(status().isBadRequest());
        mockMvc.perform(post("/api/songs/101/like").session(session)).andExpect(status().isBadRequest());
        mockMvc.perform(post("/api/playlists").param("name", " ").session(session)).andExpect(status().isBadRequest());
        mockMvc.perform(post("/api/playlists").param("name", "x".repeat(41)).session(session)).andExpect(status().isBadRequest());
        mockMvc.perform(post("/api/playlists").param("name", "Study").param("privacy", "1").session(session)).andExpect(status().isBadRequest());
        mockMvc.perform(delete("/api/playlists/77/songs/-1").session(session)).andExpect(status().isBadRequest());
        server.verify();
    }

    @Test
    void shouldReportExpiredLoginAndRemoteFailureWithoutSuccess() throws Exception {
        expect("/like", HttpMethod.POST, "{\"code\":301}", "id=101");
        server.expect(request -> assertEquals("/playlist/create", request.getURI().getPath())).andRespond(withServerError());
        mockMvc.perform(post("/api/songs/101/like").param("liked", "true").session(session))
                .andExpect(status().isUnauthorized()).andExpect(jsonPath("$.data").doesNotExist());
        mockMvc.perform(post("/api/playlists").param("name", "Study").session(session))
                .andExpect(status().isBadGateway()).andExpect(jsonPath("$.data").doesNotExist());
        server.verify();
    }

    @Test
    void shouldRejectInvalidCreatedPlaylistAndIncompleteRemovalProof() throws Exception {
        expect("/playlist/create", HttpMethod.POST, "{\"code\":200,\"playlist\":{\"id\":0,\"name\":\"Study\"}}");
        expectProfile();
        expect("/playlist/detail", HttpMethod.GET, "{\"code\":200,\"playlist\":{\"id\":77,\"creator\":{\"userId\":42}}}");
        mockMvc.perform(post("/api/playlists").param("name", "Study").session(session)).andExpect(status().isBadGateway());
        mockMvc.perform(delete("/api/playlists/77/songs/101").session(session)).andExpect(status().isBadGateway());
        server.verify();
    }
}
