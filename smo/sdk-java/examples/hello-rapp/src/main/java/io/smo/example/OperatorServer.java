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

    private interface Action {
        void run() throws IOException;
    }

    private static void allow(HttpExchange exchange, String method, Action action) throws IOException {
        if (method.equals(exchange.getRequestMethod())) {
            action.run();
        } else {
            exchange.getResponseHeaders().add("Allow", method);
            send(exchange, 405, Map.of("title", "METHOD_NOT_ALLOWED"));
        }
    }

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

    private static JsonNode readBody(HttpExchange exchange) throws IOException {
        byte[] bytes = exchange.getRequestBody().readNBytes(64 * 1024);
        return bytes.length == 0 ? JSON.createObjectNode() : JSON.readTree(bytes);
    }

    private static void send(HttpExchange exchange, int status, Object body) throws IOException {
        byte[] bytes = JSON.writeValueAsString(body).getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
    }
}
