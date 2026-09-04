package cn.itcast.demo.musicbackend.controller;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.itcast.demo.musicbackend.dto.CurrentSongContextData;
import cn.itcast.demo.musicbackend.dto.LyricsStatus;
import cn.itcast.demo.musicbackend.exception.GlobalExceptionHandler;
import cn.itcast.demo.musicbackend.exception.NotFoundException;
import cn.itcast.demo.musicbackend.service.NeteaseMusicService;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class UserMusicControllerTests {

    private NeteaseMusicService musicService;
    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        musicService = mock(NeteaseMusicService.class);
        mockMvc = MockMvcBuilders
                .standaloneSetup(new UserMusicController(musicService))
                .setControllerAdvice(new GlobalExceptionHandler())
                .build();
    }

    @Test
    void shouldReturnCurrentSongContext() throws Exception {
        when(musicService.getCurrentSongContext(185809)).thenReturn(
                new CurrentSongContextData(
                        185809,
                        "晴天",
                        "周杰伦",
                        "叶惠美",
                        "[00:01.00]故事的小黄花",
                        "",
                        LyricsStatus.AVAILABLE));

        mockMvc.perform(get("/api/songs/185809/context"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(200))
                .andExpect(jsonPath("$.data.songId").value(185809))
                .andExpect(jsonPath("$.data.artist").value("周杰伦"))
                .andExpect(jsonPath("$.data.lyricsStatus").value("AVAILABLE"));
    }

    @Test
    void shouldKeepExistingNotFoundErrorContract() throws Exception {
        when(musicService.getCurrentSongContext(999999))
                .thenThrow(new NotFoundException("歌曲不存在"));

        mockMvc.perform(get("/api/songs/999999/context"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(404))
                .andExpect(jsonPath("$.message").value("歌曲不存在"))
                .andExpect(jsonPath("$.data").doesNotExist());
    }
}
