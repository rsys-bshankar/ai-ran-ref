package io.smo.example;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import io.smo.sdk.SdkException;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The HTTP side of the rApp, on the JDK's built-in server: {@code /live} and {@code /ready} for the container health check, and
 * the operator API the package's {@code operatorUi} declares:
 * {@code GET /instances/{id}/status}, {@code GET /instances/{id}/runs}, {@code POST /instances/{id}/run}.
 * The gateway forwards {@code /rapps/{instanceId}/operator/...} here with the {@code /instances/...} part of the path.
 */
final class OperatorServer {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final Pattern ROUTE = Pattern.compile("^/instances/([^/]+)/(status|runs|run)$");

    private final HelloRapp app;
    private final HttpServer server;

    /**
     * Binds {@code port} (0 picks a free one) and prepares the routes; nothing is served until {@link #start()}. Requests are
     * handled on four daemon threads.
     */
    OperatorServer(HelloRapp app, int port) throws IOException {
        this.app = app;
        this.server = HttpServer.create(new InetSocketAddress(port), 0);
        this.server.setExecutor(Executors.newFixedThreadPool(4, r -> {
            Thread t = new Thread(r, "operator-api");
            t.setDaemon(true);
            return t;
        }));
        this.server.createContext("/", this::handle);
    }

    void start() {
        server.start();
    }

    void stop() {
        server.stop(1);
    }

    int port() {
        return server.getAddress().getPort();
    }

    /**
     * Answers every request: {@code /live} always 200, {@code /ready} 200 once the rApp is RUNNING and 503 before; the three
     * operator routes only for this process's instance id (another id or an unknown path is 404, a wrong method 405 with an
     * {@code Allow} header). An {@link SdkException} from the platform becomes 502 PLATFORM_ERROR and any other runtime failure
     * 500 INTERNAL_ERROR. An {@link IOException} (for instance a request body that is not JSON) is not caught here and goes to
     * the JDK server, so no JSON error answer is written for it. The exchange is always closed.
     */
    private void handle(HttpExchange exchange) throws IOException {
        try {
            String path = exchange.getRequestURI().getPath();
            String method = exchange.getRequestMethod();
            if ("GET".equals(method) && ("/live".equals(path) || "/ready".equals(path))) {
                boolean ok = "/live".equals(path) || app.ready();
                send(exchange, ok ? 200 : 503, Map.of("status", ok ? "ok" : "not ready"));
                return;
            }
            Matcher m = ROUTE.matcher(path);
            if (!m.matches() || !m.group(1).equals(app.instanceId())) {
                send(exchange, 404, Map.of("title", "NOT_FOUND"));
                return;
            }
            switch (m.group(2)) {
                case "status" -> allow(exchange, "GET", () -> send(exchange, 200, app.status()));
                case "runs" -> allow(exchange, "GET", () -> send(exchange, 200, app.runs(limitOf(exchange))));
                default -> allow(exchange, "POST", () -> {
                    JsonNode body = readBody(exchange);
                    send(exchange, 202, app.run(body.path("dryRun").asBoolean(false)));
                });
            }
        } catch (SdkException e) {
            // the platform refused or was unreachable: say so, do not crash the rApp
            send(exchange, 502, Map.of("title", "PLATFORM_ERROR", "status", e.status(), "detail", String.valueOf(e.getMessage())));
        } catch (RuntimeException e) {
            send(exchange, 500, Map.of("title", "INTERNAL_ERROR"));
        } finally {
            exchange.close();
        }
    }

    /**
     * The body of one operator route, run by {@code allow} once the HTTP method has been checked. It may throw {@link IOException}
     * (reading the request or writing the answer).
     */
    private interface Action {
        /**
         * Runs the route once the method has been checked.
         */
        void run() throws IOException;
    }

    /**
     * Runs {@code action} when the request uses {@code method}; otherwise answers 405 with {@code Allow: method}.
     */
    private static void allow(HttpExchange exchange, String method, Action action) throws IOException {
        if (method.equals(exchange.getRequestMethod())) {
            action.run();
        } else {
            exchange.getResponseHeaders().add("Allow", method);
            send(exchange, 405, Map.of("title", "METHOD_NOT_ALLOWED"));
        }
    }

    /**
     * Reads {@code limit=<n>} from the query string, kept between 1 and 50; 20 when it is absent or not a number.
     */
    private static int limitOf(HttpExchange exchange) {
        String query = exchange.getRequestURI().getQuery();
        if (query != null) {
            for (String pair : query.split("&")) {
                if (pair.startsWith("limit=")) {
                    try {
                        return Math.max(1, Math.min(Integer.parseInt(pair.substring(6)), 50));
                    } catch (NumberFormatException e) {
                        break;
                    }
                }
            }
        }
        return 20;
    }

    /**
     * Reads the request body as JSON, at most 64 KiB (the rest is not read); an empty body is an empty object.
     */
    private static JsonNode readBody(HttpExchange exchange) throws IOException {
        byte[] bytes = exchange.getRequestBody().readNBytes(64 * 1024);
        return bytes.length == 0 ? JSON.createObjectNode() : JSON.readTree(bytes);
    }

    /**
     * Writes {@code body} as the JSON answer with {@code status}.
     */
    private static void send(HttpExchange exchange, int status, Object body) throws IOException {
        byte[] bytes = JSON.writeValueAsString(body).getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
    }
}
