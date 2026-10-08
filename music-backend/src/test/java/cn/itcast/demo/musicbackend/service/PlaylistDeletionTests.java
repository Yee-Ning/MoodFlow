package cn.itcast.demo.musicbackend.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withServerError;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.itcast.demo.musicbackend.controller.UserMusicController;
import cn.itcast.demo.musicbackend.exception.GlobalExceptionHandler;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.client.RestClient;

class PlaylistDeletionTests {
    private MockRestServiceServer server;
    private MockMvc mvc;

    @BeforeEach
    void setUp() {
        RestClient.Builder builder = RestClient.builder().baseUrl("http://localhost:3000");
        server = MockRestServiceServer.bindTo(builder).build();
        mvc = MockMvcBuilders.standaloneSetup(new UserMusicController(new NeteaseMusicService(builder.build())))
                .setControllerAdvice(new GlobalExceptionHandler()).build();
    }

    private void expect(String path, HttpMethod method, String body) {
        server.expect(request -> {
            assertEquals(path, request.getURI().getPath());
            assertEquals(method, request.getMethod());
        }).andRespond(withSuccess(body, MediaType.APPLICATION_JSON));
    }

    private void profile() {
        expect("/user/account", HttpMethod.GET,
                "{\"code\":200,\"profile\":{\"userId\":42,\"nickname\":\"Test\",\"avatarUrl\":\"\"}}");
    }

    private void detail(String fields) {
        expect("/playlist/detail", HttpMethod.GET, "{\"code\":200,\"playlist\":{" + fields + "}}");
    }

    @Test
    void deletesOnceAndReadsBackAllPages() throws Exception {
        profile();
        detail("\"id\":77,\"creator\":{\"userId\":42},\"subscribed\":false,\"specialType\":0");
        expect("/playlist/delete", HttpMethod.POST, "{\"code\":200}");
        profile();
        expect("/user/playlist", HttpMethod.GET, """
                {"code":200,"more":true,"playlist":[{"id":88,"name":"Other","creator":{"nickname":"Test"},"subscribed":false}]}
                """);
        expect("/user/playlist", HttpMethod.GET, "{\"code\":200,\"more\":false,\"playlist\":[]}");
        mvc.perform(delete("/api/playlists/77").param("confirmed", "true")
                        .sessionAttr(AuthService.NETEASE_COOKIE, "MUSIC_U=fake-test"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data.playlistId").value(77))
                .andExpect(jsonPath("$.data.status").value("deleted"));
        server.verify();
    }

    @ParameterizedTest
    @ValueSource(strings = {"false", ""})
    void noConfirmationNeverReadsOrWrites(String confirmed) throws Exception {
        var request = delete("/api/playlists/77");
        if (!confirmed.isEmpty()) request.param("confirmed", confirmed);
        mvc.perform(request).andExpect(status().isUnprocessableEntity());
        server.verify();
    }

    @Test
    void requiresAuthenticatedSession() throws Exception {
        mvc.perform(delete("/api/playlists/77").param("confirmed", "true"))
                .andExpect(status().isUnauthorized());
        server.verify();
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "\"id\":77,\"creator\":{\"userId\":99},\"subscribed\":false,\"specialType\":0",
            "\"id\":77,\"creator\":{\"userId\":42},\"subscribed\":true,\"specialType\":0",
            "\"id\":77,\"creator\":{\"userId\":42},\"subscribed\":false,\"specialType\":5"})
    void rejectsForeignSubscribedAndSystemPlaylistsWithoutDispatch(String fields) throws Exception {
        profile();
        detail(fields);
        mvc.perform(delete("/api/playlists/77").param("confirmed", "true")
                        .sessionAttr(AuthService.NETEASE_COOKIE, "MUSIC_U=fake-test"))
                .andExpect(status().isForbidden());
        server.verify();
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "\"id\":77,\"subscribed\":false,\"specialType\":0",
            "\"id\":77,\"creator\":{\"userId\":42},\"subscribed\":false",
            "\"id\":78,\"creator\":{\"userId\":42},\"subscribed\":false,\"specialType\":0",
            "\"id\":77,\"creator\":{\"userId\":42.5},\"subscribed\":false,\"specialType\":0"})
    void rejectsUnknownOwnershipOrMismatchedTargetWithoutDispatch(String fields) throws Exception {
        profile();
        detail(fields);
        mvc.perform(delete("/api/playlists/77").param("confirmed", "true")
                        .sessionAttr(AuthService.NETEASE_COOKIE, "MUSIC_U=fake-test"))
                .andExpect(status().isUnprocessableEntity());
        server.verify();
    }

    @Test
    void retainedTargetIsUnknownAndNeverDispatchedAgain() throws Exception {
        profile();
        detail("\"id\":77,\"creator\":{\"userId\":42},\"subscribed\":false,\"specialType\":0");
        expect("/playlist/delete", HttpMethod.POST, "{\"code\":200}");
        profile();
        expect("/user/playlist", HttpMethod.GET, """
                {"code":200,"more":false,"playlist":[{"id":77,"name":"Target","creator":{"nickname":"Test"},"subscribed":false}]}
                """);
        mvc.perform(delete("/api/playlists/77").param("confirmed", "true")
                        .sessionAttr(AuthService.NETEASE_COOKIE, "MUSIC_U=fake-test"))
                .andExpect(status().isBadGateway()).andExpect(jsonPath("$.message").value(
                        "删除请求已提交，但自有歌单读回仍包含目标；效果未知，禁止重复删除"));
        server.verify();
    }

    @Test
    void readbackFailureAfterDispatchPreservesGatewayError() throws Exception {
        profile();
        detail("\"id\":77,\"creator\":{\"userId\":42},\"subscribed\":false,\"specialType\":0");
        expect("/playlist/delete", HttpMethod.POST, "{\"code\":200}");
        server.expect(request -> assertEquals("/user/account", request.getURI().getPath()))
                .andRespond(withServerError());
        mvc.perform(delete("/api/playlists/77").param("confirmed", "true")
                        .sessionAttr(AuthService.NETEASE_COOKIE, "MUSIC_U=fake-test"))
                .andExpect(status().isBadGateway());
        server.verify();
    }

    @Test
    void rejectsInvalidIdAndBooleanBeforeDispatch() throws Exception {
        mvc.perform(delete("/api/playlists/0").param("confirmed", "true")).andExpect(status().isBadRequest());
        mvc.perform(delete("/api/playlists/77").param("confirmed", "maybe")).andExpect(status().isBadRequest());
        server.verify();
    }

    @Test
    void postDispatchAuthRejectionRemainsUnknownNotDefiniteFailure() throws Exception {
        profile();
        detail("\"id\":77,\"creator\":{\"userId\":42},\"subscribed\":false,\"specialType\":0");
        expect("/playlist/delete", HttpMethod.POST, "{\"code\":200}");
        expect("/user/account", HttpMethod.GET, "{\"code\":301}");
        mvc.perform(delete("/api/playlists/77").param("confirmed", "true")
                        .sessionAttr(AuthService.NETEASE_COOKIE, "MUSIC_U=fake-test"))
                .andExpect(status().isBadGateway());
        server.verify();
    }

    @Test
    void incompletePaginationCannotProveDeletion() throws Exception {
        profile();
        detail("\"id\":77,\"creator\":{\"userId\":42},\"subscribed\":false,\"specialType\":0");
        expect("/playlist/delete", HttpMethod.POST, "{\"code\":200}");
        profile();
        expect("/user/playlist", HttpMethod.GET, "{\"code\":200,\"more\":true,\"playlist\":[]}");
        mvc.perform(delete("/api/playlists/77").param("confirmed", "true")
                        .sessionAttr(AuthService.NETEASE_COOKIE, "MUSIC_U=fake-test"))
                .andExpect(status().isBadGateway());
        server.verify();
    }
}
