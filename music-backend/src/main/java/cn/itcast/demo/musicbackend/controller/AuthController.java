package cn.itcast.demo.musicbackend.controller;

import cn.itcast.demo.musicbackend.common.ApiResponse;
import cn.itcast.demo.musicbackend.dto.AuthStatusData;
import cn.itcast.demo.musicbackend.dto.QrCheckData;
import cn.itcast.demo.musicbackend.dto.QrCreateData;
import cn.itcast.demo.musicbackend.dto.QrKeyData;
import cn.itcast.demo.musicbackend.service.AuthService;
import jakarta.servlet.http.HttpServletRequest;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/auth")
public class AuthController {

    private final AuthService authService;

    public AuthController(AuthService authService) {
        this.authService = authService;
    }

    @GetMapping("/qr/key")
    public ApiResponse<QrKeyData> createQrKey() {
        return ApiResponse.success(authService.createQrKey());
    }

    @GetMapping("/qr/create")
    public ApiResponse<QrCreateData> createQrCode(@RequestParam(required = false) String key) {
        return ApiResponse.success(authService.createQrCode(key));
    }

    @GetMapping("/qr/check")
    public ApiResponse<QrCheckData> checkQrCode(
            @RequestParam(required = false) String key,
            HttpServletRequest request) {
        return ApiResponse.success(authService.checkQrCode(key, request));
    }

    @GetMapping("/status")
    public ApiResponse<AuthStatusData> getLoginStatus(HttpServletRequest request) {
        return ApiResponse.success(authService.getLoginStatus(request));
    }

    @PostMapping("/logout")
    public ApiResponse<AuthStatusData> logout(HttpServletRequest request) {
        return ApiResponse.success(authService.logout(request));
    }
}
