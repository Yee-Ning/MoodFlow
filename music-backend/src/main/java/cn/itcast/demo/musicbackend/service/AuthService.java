package cn.itcast.demo.musicbackend.service;

import java.net.SocketTimeoutException;
import java.net.http.HttpTimeoutException;
import java.util.Map;

import cn.itcast.demo.musicbackend.dto.AuthStatusData;
import cn.itcast.demo.musicbackend.dto.QrCheckData;
import cn.itcast.demo.musicbackend.dto.QrCreateData;
import cn.itcast.demo.musicbackend.dto.QrKeyData;
import cn.itcast.demo.musicbackend.exception.BadRequestException;
import cn.itcast.demo.musicbackend.exception.NeteaseApiException;
import cn.itcast.demo.musicbackend.exception.NeteaseApiTimeoutException;
import cn.itcast.demo.musicbackend.exception.UnauthorizedException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpSession;

import org.springframework.stereotype.Service;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

@Service
public class AuthService {

    static final String NETEASE_COOKIE = "NETEASE_COOKIE";

    private final RestClient neteaseRestClient;
    private final NeteaseMusicService neteaseMusicService;

    public AuthService(RestClient neteaseRestClient, NeteaseMusicService neteaseMusicService) {
        this.neteaseRestClient = neteaseRestClient;
        this.neteaseMusicService = neteaseMusicService;
    }

    public QrKeyData createQrKey() {
        Map<String, Object> response = get("/login/qr/key", Map.of());
        Map<String, Object> data = requiredMap(response, "data");
        return new QrKeyData(requiredString(data, "unikey"));
    }

    public QrCreateData createQrCode(String key) {
        validateKey(key);
        Map<String, Object> response = get("/login/qr/create", Map.of(
                "key", key,
                "qrimg", true));
        Map<String, Object> data = requiredMap(response, "data");
        return new QrCreateData(
                requiredString(data, "qrurl"),
                requiredString(data, "qrimg"));
    }

    public QrCheckData checkQrCode(String key, HttpServletRequest request) {
        validateKey(key);
        Map<String, Object> response = get("/login/qr/check", Map.of("key", key));
        int statusCode = requiredInt(response, "code");
        QrStatus status = QrStatus.fromCode(statusCode);

        if (status == QrStatus.LOGIN_SUCCESS) {
            String cookie = requiredString(response, "cookie");
            request.getSession(true).setAttribute(NETEASE_COOKIE, cookie);
        }

        return new QrCheckData(
                status.code,
                status.status,
                status.message,
                status == QrStatus.LOGIN_SUCCESS);
    }

    public AuthStatusData getLoginStatus(HttpServletRequest request) {
        HttpSession session = request.getSession(false);
        if (session == null) {
            return new AuthStatusData(false);
        }
        Object value = session.getAttribute(NETEASE_COOKIE);
        if (!(value instanceof String cookie) || cookie.isBlank()) {
            session.removeAttribute(NETEASE_COOKIE);
            return new AuthStatusData(false);
        }

        try {
            neteaseMusicService.getCurrentUserProfile(request);
            return new AuthStatusData(true);
        } catch (UnauthorizedException exception) {
            // Only confirmed invalid credentials are cleared; upstream failures
            // propagate through the existing gateway error response.
            session.removeAttribute(NETEASE_COOKIE);
            return new AuthStatusData(false);
        }
    }

    public AuthStatusData logout(HttpServletRequest request) {
        HttpSession session = request.getSession(false);
        if (session != null) {
            session.removeAttribute(NETEASE_COOKIE);
            session.invalidate();
        }
        return new AuthStatusData(false);
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> get(String path, Map<String, Object> queryParams) {
        try {
            Map<String, Object> response = neteaseRestClient.get()
                    .uri(uriBuilder -> {
                        uriBuilder.path(path);
                        queryParams.forEach(uriBuilder::queryParam);
                        return uriBuilder
                                .queryParam("timestamp", System.currentTimeMillis())
                                .build();
                    })
                    .retrieve()
                    .body(Map.class);
            if (response == null) {
                throw new NeteaseApiException("网易云 API 返回空响应");
            }
            return response;
        } catch (ResourceAccessException exception) {
            if (hasCause(exception, SocketTimeoutException.class)
                    || hasCause(exception, HttpTimeoutException.class)) {
                throw new NeteaseApiTimeoutException("网易云 API 请求超时", exception);
            }
            throw new NeteaseApiException("无法连接网易云 API", exception);
        } catch (RestClientException exception) {
            throw new NeteaseApiException("网易云 API 响应异常", exception);
        }
    }

    private void validateKey(String key) {
        if (key == null || key.isBlank()) {
            throw new BadRequestException("key 不能为空");
        }
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> requiredMap(Map<String, Object> source, String name) {
        Object value = source.get(name);
        if (value instanceof Map<?, ?> map) {
            return (Map<String, Object>) map;
        }
        throw invalidResponse(name);
    }

    private String requiredString(Map<String, Object> source, String name) {
        Object value = source.get(name);
        if (value instanceof String stringValue && !stringValue.isBlank()) {
            return stringValue;
        }
        throw invalidResponse(name);
    }

    private int requiredInt(Map<String, Object> source, String name) {
        Object value = source.get(name);
        if (value instanceof Number number) {
            return number.intValue();
        }
        throw invalidResponse(name);
    }

    private NeteaseApiException invalidResponse(String fieldName) {
        return new NeteaseApiException("网易云 API 响应缺少有效字段: " + fieldName);
    }

    private boolean hasCause(Throwable throwable, Class<? extends Throwable> causeType) {
        Throwable current = throwable;
        while (current != null) {
            if (causeType.isInstance(current)) {
                return true;
            }
            current = current.getCause();
        }
        return false;
    }

    private enum QrStatus {
        EXPIRED(800, "EXPIRED", "二维码过期"),
        WAITING_SCAN(801, "WAITING_SCAN", "等待扫码"),
        WAITING_CONFIRM(802, "WAITING_CONFIRM", "已扫码，等待手机确认"),
        LOGIN_SUCCESS(803, "LOGIN_SUCCESS", "登录成功");

        private final int code;
        private final String status;
        private final String message;

        QrStatus(int code, String status, String message) {
            this.code = code;
            this.status = status;
            this.message = message;
        }

        private static QrStatus fromCode(int code) {
            for (QrStatus status : values()) {
                if (status.code == code) {
                    return status;
                }
            }
            throw new NeteaseApiException("网易云 API 返回未知扫码状态: " + code);
        }
    }
}
