package cn.itcast.demo.musicbackend.exception;

public class NeteaseApiTimeoutException extends RuntimeException {

    public NeteaseApiTimeoutException(String message, Throwable cause) {
        super(message, cause);
    }
}
