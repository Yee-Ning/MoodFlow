package cn.itcast.demo.musicbackend.exception;

public class NeteaseApiException extends RuntimeException {

    public NeteaseApiException(String message) {
        super(message);
    }

    public NeteaseApiException(String message, Throwable cause) {
        super(message, cause);
    }
}
