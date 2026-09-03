package cn.itcast.demo.musicbackend.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.http.MediaType.APPLICATION_JSON;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

import java.net.URI;
import java.util.Arrays;
import java.util.stream.Stream;

import cn.itcast.demo.musicbackend.dto.QrCheckData;
import cn.itcast.demo.musicbackend.exception.BadRequestException;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

class AuthServiceTests {

    private MockRestServiceServer server;
    private AuthService authService;

    @BeforeEach
    void setUp() {
        RestClient.Builder builder = RestClient.builder().baseUrl("http://localhost:3000");
        server = MockRestServiceServer.bindTo(builder).build();
        authService = new AuthService(builder.build());
    }

    @ParameterizedTest
    @MethodSource("nonLoginStatuses")
    void shouldMapNonLoginQrStatuses(
            int code,
            String expectedStatus,
            String expectedMessage) {
        server.expect(request -> assertQrCheckRequest(request.getURI(), "test-key"))
                .andRespond(withSuccess("{\"code\":" + code + "}", APPLICATION_JSON));
        MockHttpServletRequest request = new MockHttpServletRequest();

        QrCheckData result = authService.checkQrCode("test-key", request);

        assertEquals(code, result.statusCode());
        assertEquals(expectedStatus, result.status());
        assertEquals(expectedMessage, result.statusMessage());
        assertFalse(result.loggedIn());
        assertNull(request.getSession(false));
        server.verify();
    }

    @Test
    void shouldStoreCookieInSessionAfterSuccessfulLogin() {
        server.expect(request -> assertQrCheckRequest(request.getURI(), "test-key"))
                .andRespond(withSuccess(
                        "{\"code\":803,\"cookie\":\"MUSIC_U=sensitive-cookie\"}",
                        APPLICATION_JSON));
        MockHttpServletRequest request = new MockHttpServletRequest();

        QrCheckData result = authService.checkQrCode("test-key", request);

        assertEquals(803, result.statusCode());
        assertEquals("LOGIN_SUCCESS", result.status());
        assertEquals("登录成功", result.statusMessage());
        assertTrue(result.loggedIn());
        assertEquals(
                "MUSIC_U=sensitive-cookie",
                request.getSession(false).getAttribute(AuthService.NETEASE_COOKIE));
        server.verify();
    }

    @Test
    void shouldRejectBlankKey() {
        BadRequestException exception = assertThrows(
                BadRequestException.class,
                () -> authService.createQrCode("  "));

        assertEquals("key 不能为空", exception.getMessage());
        server.verify();
    }

    private static Stream<Arguments> nonLoginStatuses() {
        return Stream.of(
                Arguments.of(800, "EXPIRED", "二维码过期"),
                Arguments.of(801, "WAITING_SCAN", "等待扫码"),
                Arguments.of(802, "WAITING_CONFIRM", "已扫码，等待手机确认"));
    }

    private void assertQrCheckRequest(URI uri, String expectedKey) {
        assertEquals("/login/qr/check", uri.getPath());
        String query = uri.getQuery();
        assertTrue(Arrays.asList(query.split("&")).contains("key=" + expectedKey));
        assertTrue(Arrays.stream(query.split("&")).anyMatch(value -> value.startsWith("timestamp=")));
    }
}
