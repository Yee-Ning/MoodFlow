package cn.itcast.demo.musicbackend.exception;

public class UnprocessableRequestException extends RuntimeException {
    public UnprocessableRequestException(String message) {
        super(message);
    }
}
