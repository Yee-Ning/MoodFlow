import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.regex.Pattern;

/** Deployment probe using only the JRE; never creates sessions or calls upstream. */
public final class HttpHealthCheck {
    private static final Pattern SUCCESS_CODE = Pattern.compile("\\\"code\\\"\\s*:\\s*200(?:\\s*[,}])");

    public static void main(String[] args) {
        try {
            var request = HttpRequest.newBuilder(URI.create(args[0]))
                    .timeout(Duration.ofSeconds(3)).GET().build();
            var response = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3))
                    .build().send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() != 200 || !SUCCESS_CODE.matcher(response.body()).find()) {
                System.exit(1);
            }
        } catch (Exception ignored) {
            System.exit(1);
        }
    }
}
