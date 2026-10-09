package io.smo.sdk;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.Set;

/**
 * A platform route the SDK calls: the R1 Termination path template ({@code /<module>/...}, each path parameter written
 * {@code {}}), plus the body fields and the query parameters the client sends. {@code RoutesContractTest} compares every
 * route of {@link Routes} with the committed {@code docs/openapi/*.json}, so a route that is renamed, removed or given a
 * new required body property fails the SDK build instead of failing at run time.
 *
 * @param bodyFields the top-level JSON properties the client always sets in the body (empty: no body, or a free-form object)
 * @param query      the query parameters the client may send
 */
public record Route(String method, String template, Set<String> bodyFields, Set<String> query) {

    public static Route get(String template, String... query) {
        return new Route("GET", template, Set.of(), Set.of(query));
    }

    public static Route delete(String template, String... query) {
        return new Route("DELETE", template, Set.of(), Set.of(query));
    }

    public static Route post(String template, Set<String> bodyFields, String... query) {
        return new Route("POST", template, bodyFields, Set.of(query));
    }

    public static Route put(String template, Set<String> bodyFields, String... query) {
        return new Route("PUT", template, bodyFields, Set.of(query));
    }

    /** The path with each {@code {}} replaced by the next argument, percent-encoded as one path segment. */
    public String path(Object... args) {
        StringBuilder out = new StringBuilder();
        int next = 0;
        int from = 0;
        for (int at; (at = template.indexOf("{}", from)) >= 0; from = at + 2) {
            if (next >= args.length) {
                throw new IllegalArgumentException("route " + template + " needs more path arguments");
            }
            out.append(template, from, at).append(segment(String.valueOf(args[next++])));
        }
        if (next != args.length) {
            throw new IllegalArgumentException("route " + template + " takes " + next + " path arguments, not " + args.length);
        }
        return out.append(template.substring(from)).toString();
    }

    private static String segment(String value) {
        if (value.isEmpty()) {
            throw new IllegalArgumentException("a path argument is empty");
        }
        return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
    }
}
