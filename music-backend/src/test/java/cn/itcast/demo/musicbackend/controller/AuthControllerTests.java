package cn.itcast.demo.musicbackend.controller;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.http.MediaType.APPLICATION_JSON;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withException;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withStatus;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.io.IOException;
import java.net.SocketTimeoutException;

import cn.itcast.demo.musicbackend.exception.GlobalExceptionHandler;
import cn.itcast.demo.musicbackend.service.AuthService;
import cn.itcast.demo.musicbackend.service.NeteaseMusicService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.test.web.client.ResponseActions;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.client.RestClient;

class AuthControllerTests {

    private static final String COOKIE_ATTRIBUTE = "NETEASE_COOKIE";
    private static final String COOKIE = "MUSIC_U=test-session; Path=/; HttpOnly;";
    private static final String VALID_PROFILE = """
            {"code":200,"profile":{"userId":42,"nickname":"Test User"}}
            """;

    private MockRestServiceServer server;
    private MockMvc mockMvc;
    private MockHttpSession session;

    @BeforeEach
    void setUp() {
        RestClient.Builder builder = RestClient.builder().baseUrl("http://localhost:3000");
        server = MockRestServiceServer.bindTo(builder).build();
        RestClient client = builder.build();
        AuthService authService = new AuthService(client, new NeteaseMusicService(client));
        mockMvc = MockMvcBuilders.standaloneSetup(new AuthController(authService))
                .setControllerAdvice(new GlobalExceptionHandler()).build();
        session = new MockHttpSession();
        session.setAttribute(COOKIE_ATTRIBUTE, COOKIE);
    }

    private ResponseActions expectProfileRequest() {
        return server.expect(request -> {
            assertEquals("/user/account", request.getURI().getPath());
            assertEquals(HttpMethod.GET, request.getMethod());
            assertEquals("MUSIC_U=test-session", request.getHeaders().getFirst(HttpHeaders.COOKIE));
            assertTrue(request.getURI().getQuery().startsWith("timestamp="));
        });
    }

    @Test
    void shouldReportLoggedOutWithoutCreatingSessionOrCallingUpstream() throws Exception {
        var result = mockMvc.perform(get("/api/auth/status"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(200))
                .andExpect(jsonPath("$.data.loggedIn").value(false))
                .andReturn();
        assertNull(result.getRequest().getSession(false));
        server.verify();
    }

    @Test
    void shouldReportLoggedOutForSessionWithoutCredentials() throws Exception {
        session.removeAttribute(COOKIE_ATTRIBUTE);
        mockMvc.perform(get("/api/auth/status").session(session))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.loggedIn").value(false));
        server.verify();
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "  ", "Path=/; HttpOnly;"})
    void shouldClearInvalidLocalCredentialsWithoutNetworkCall(String cookie) throws Exception {
        session.setAttribute(COOKIE_ATTRIBUTE, cookie);
        mockMvc.perform(get("/api/auth/status").session(session))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.loggedIn").value(false));
        assertNull(session.getAttribute(COOKIE_ATTRIBUTE));
        server.verify();
    }

    @Test
    void shouldReportLoggedInOnlyAfterRemoteValidation() throws Exception {
        expectProfileRequest().andRespond(withSuccess(VALID_PROFILE, APPLICATION_JSON));
        mockMvc.perform(get("/api/auth/status").session(session))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(200))
                .andExpect(jsonPath("$.data.loggedIn").value(true));
        assertEquals(COOKIE, session.getAttribute(COOKIE_ATTRIBUTE));
        server.verify();
    }

    @ParameterizedTest
    @ValueSource(strings = {"{\"code\":301}", "{\"code\":200,\"profile\":null}", "{\"code\":200}"})
    void shouldClearExpiredCredentialsAndKeepStatusContract(String response) throws Exception {
        expectProfileRequest().andRespond(withSuccess(response, APPLICATION_JSON));
        mockMvc.perform(get("/api/auth/status").session(session))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(200))
                .andExpect(jsonPath("$.data.loggedIn").value(false));
        assertNull(session.getAttribute(COOKIE_ATTRIBUTE));
        mockMvc.perform(get("/api/auth/status").session(session))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.loggedIn").value(false));
        server.verify();
    }

    @Test
    void shouldRecognizeExpirationAfterAnEarlierSuccessfulCheck() throws Exception {
        expectProfileRequest().andRespond(withSuccess(VALID_PROFILE, APPLICATION_JSON));
        expectProfileRequest().andRespond(withSuccess("{\"code\":301}", APPLICATION_JSON));
        mockMvc.perform(get("/api/auth/status").session(session))
                .andExpect(jsonPath("$.data.loggedIn").value(true));
        mockMvc.perform(get("/api/auth/status").session(session))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.loggedIn").value(false));
        assertNull(session.getAttribute(COOKIE_ATTRIBUTE));
        server.verify();
    }

    @Test
    void shouldRecognizeHttpUnauthorizedAsExpiredLogin() throws Exception {
        expectProfileRequest().andRespond(withStatus(HttpStatus.UNAUTHORIZED));
        mockMvc.perform(get("/api/auth/status").session(session))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.loggedIn").value(false));
        assertNull(session.getAttribute(COOKIE_ATTRIBUTE));
        server.verify();
    }

    @ParameterizedTest
    @ValueSource(ints = {403, 500, 503})
    void shouldKeepCredentialsOnRemoteHttpFailureAndAllowRetry(int remoteStatus) throws Exception {
        expectProfileRequest().andRespond(withStatus(HttpStatus.valueOf(remoteStatus)));
        expectProfileRequest().andRespond(withSuccess(VALID_PROFILE, APPLICATION_JSON));
        mockMvc.perform(get("/api/auth/status").session(session))
                .andExpect(status().isBadGateway())
                .andExpect(jsonPath("$.code").value(502))
                .andExpect(jsonPath("$.data").doesNotExist());
        assertEquals(COOKIE, session.getAttribute(COOKIE_ATTRIBUTE));
        mockMvc.perform(get("/api/auth/status").session(session))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.loggedIn").value(true));
        server.verify();
    }

    @Test
    void shouldKeepCredentialsAndReturnGatewayTimeoutOnRemoteTimeout() throws Exception {
        expectProfileRequest().andRespond(withException(new SocketTimeoutException("test timeout")));
        mockMvc.perform(get("/api/auth/status").session(session))
                .andExpect(status().isGatewayTimeout())
                .andExpect(jsonPath("$.code").value(504));
        assertEquals(COOKIE, session.getAttribute(COOKIE_ATTRIBUTE));
        server.verify();
    }

    @Test
    void shouldKeepCredentialsOnConnectionFailure() throws Exception {
        expectProfileRequest().andRespond(withException(new IOException("test unavailable")));
        mockMvc.perform(get("/api/auth/status").session(session))
                .andExpect(status().isBadGateway());
        assertEquals(COOKIE, session.getAttribute(COOKIE_ATTRIBUTE));
        server.verify();
    }

    @Test
    void shouldKeepCredentialsOnMalformedRemoteResponse() throws Exception {
        expectProfileRequest().andRespond(withSuccess("""
                {"code":200,"profile":{"userId":"invalid","nickname":"Test User"}}
                """, APPLICATION_JSON));
        mockMvc.perform(get("/api/auth/status").session(session))
                .andExpect(status().isBadGateway());
        assertEquals(COOKIE, session.getAttribute(COOKIE_ATTRIBUTE));
        server.verify();
    }

    @Test
    void shouldPreserveLogoutContract() throws Exception {
        mockMvc.perform(post("/api/auth/logout").session(session))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.loggedIn").value(false));
        assertTrue(session.isInvalid());
        server.verify();
    }
}
