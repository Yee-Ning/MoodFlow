package cn.itcast.demo.musicbackend.controller;

import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import cn.itcast.demo.musicbackend.dto.SongCommentSummaryData;
import cn.itcast.demo.musicbackend.exception.BadRequestException;
import cn.itcast.demo.musicbackend.exception.GlobalExceptionHandler;
import cn.itcast.demo.musicbackend.exception.NeteaseApiException;
import cn.itcast.demo.musicbackend.exception.ReadQuotaExceededException;
import cn.itcast.demo.musicbackend.service.NeteaseMusicService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class SongCommentSummaryControllerTests {
    private NeteaseMusicService service;
    private MockMvc mvc;

    @BeforeEach
    void setUp() {
        service = mock(NeteaseMusicService.class);
        mvc = MockMvcBuilders.standaloneSetup(new UserMusicController(service))
                .setControllerAdvice(new GlobalExceptionHandler()).build();
    }

    @Test
    void shouldReturnPublicCountOnly() throws Exception {
        when(service.getSongCommentSummary(185809)).thenReturn(new SongCommentSummaryData(185809, 68320));
        mvc.perform(get("/api/songs/185809/comment-summary"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.code").value(200))
                .andExpect(jsonPath("$.data.songId").value(185809))
                .andExpect(jsonPath("$.data.commentCount").value(68320))
                .andExpect(jsonPath("$.data.comments").doesNotExist());
    }

    @Test
    void shouldKeepValidationAndErrorContracts() throws Exception {
        when(service.getSongCommentSummary(0)).thenThrow(new BadRequestException("invalid ID"));
        when(service.getSongCommentSummary(185809)).thenThrow(new NeteaseApiException("unknown total"));
        mvc.perform(get("/api/songs/0/comment-summary")).andExpect(status().isBadRequest());
        mvc.perform(get("/api/songs/not-an-id/comment-summary")).andExpect(status().isBadRequest());
        mvc.perform(get("/api/songs/185809/comment-summary"))
                .andExpect(status().isBadGateway()).andExpect(jsonPath("$.data").doesNotExist());
    }

    @Test
    void shouldExposeQuotaWithoutInventedTotal() throws Exception {
        when(service.getSongCommentSummary(185809)).thenThrow(new ReadQuotaExceededException());
        mvc.perform(get("/api/songs/185809/comment-summary"))
                .andExpect(status().isTooManyRequests()).andExpect(jsonPath("$.data").doesNotExist());
    }
}
