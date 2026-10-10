package cn.itcast.demo.musicbackend.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.http.MediaType.APPLICATION_JSON;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withServerError;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withStatus;

import cn.itcast.demo.musicbackend.exception.BadRequestException;
import cn.itcast.demo.musicbackend.exception.NeteaseApiException;
import cn.itcast.demo.musicbackend.exception.ReadQuotaExceededException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;
import org.springframework.http.HttpStatus;

class SongCommentSummaryServiceTests {
    private MockRestServiceServer server;
    private NeteaseMusicService service;

    @BeforeEach
    void setUp() {
        RestClient.Builder builder = RestClient.builder().baseUrl("http://localhost:3000");
        server = MockRestServiceServer.bindTo(builder).build();
        service = new NeteaseMusicService(builder.build());
    }

    private void expectTotal(String total) {
        server.expect(request -> {
            assertEquals("/comment/music", request.getURI().getPath());
            String query = request.getURI().getQuery();
            for (String field : new String[]{"id=185809", "limit=1", "offset=0", "before=0"}) {
                assertTrue(query.contains(field));
            }
            assertNull(request.getHeaders().getFirst("Cookie"));
        }).andRespond(withSuccess("{\"code\":200,\"total\":" + total + ",\"comments\":[]}", APPLICATION_JSON));
    }

    @Test
    void shouldUseTotalRatherThanPageLengthAndPreserveZero() {
        expectTotal("68320");
        expectTotal("0");
        assertEquals(68320, service.getSongCommentSummary(185809).commentCount());
        assertEquals(0, service.getSongCommentSummary(185809).commentCount());
        server.verify();
    }

    @Test
    void shouldRejectInvalidIdsBeforeRequest() {
        assertThrows(BadRequestException.class, () -> service.getSongCommentSummary(0));
        assertThrows(BadRequestException.class, () -> service.getSongCommentSummary(-1));
        server.verify();
    }

    @Test
    void shouldRejectMalformedTotalsWithoutGuessing() {
        for (String total : new String[]{"null", "-1", "true", "1.5", "\"100\""}) {
            expectTotal(total);
        }
        for (int i = 0; i < 5; i++) {
            assertThrows(NeteaseApiException.class, () -> service.getSongCommentSummary(185809));
        }
        server.verify();
    }

    @Test
    void shouldPropagateMissingTotalAndUpstreamFailureWithoutRetry() {
        server.expect(request -> {}).andRespond(withSuccess("{\"code\":200,\"comments\":[]}", APPLICATION_JSON));
        server.expect(request -> {}).andRespond(withServerError());
        assertThrows(NeteaseApiException.class, () -> service.getSongCommentSummary(185809));
        assertThrows(NeteaseApiException.class, () -> service.getSongCommentSummary(185809));
        server.verify();
    }

    @Test
    void shouldPreserveUpstreamQuotaForNewReadOnly() {
        server.expect(request -> {}).andRespond(withStatus(HttpStatus.TOO_MANY_REQUESTS));
        assertThrows(ReadQuotaExceededException.class, () -> service.getSongCommentSummary(185809));
        server.verify();
    }
}
