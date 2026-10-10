package cn.itcast.demo.musicbackend.exception;

public class ReadQuotaExceededException extends RuntimeException {
    public ReadQuotaExceededException() {
        super("评论读取配额已用完，请稍后再试");
    }
}
