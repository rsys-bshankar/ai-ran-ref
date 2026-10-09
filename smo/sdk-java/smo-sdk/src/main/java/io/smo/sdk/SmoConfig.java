package io.smo.sdk;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.function.Function;

/**
 * Where the platform is and who this process is. {@link #fromEnv()} reads the same variables the Python SDK's
 * {@code R1Client} reads, so a Java rApp is configured exactly as the sample rApps are in {@code docker-compose.yml}.
 *
 * <ul>
 *   <li>{@code R1_GATEWAY_URL}: R1 Termination (default {@code http://r1-termination:8000});</li>
 *   <li>{@code SMO_INVOKER_ID} and {@code SMO_INVOKER_SECRET}: a pre-issued identity, as handed out by
 *       {@code POST /rapp-mgmt/instances/{id}/credentials}. Unset, the SDK enrols a new invoker at SME on first use;</li>
 *   <li>{@code SMO_BOOTSTRAP_KEY} or {@code SMO_BOOTSTRAP_KEY_FILE}: the {@code X-Bootstrap-Key} for {@code GET /bootstrap}
 *       when the gateway asks for one;</li>
 *   <li>{@code SMO_IDENTITY_KIND}: {@code rapp} (the default here) or {@code module}; a module presents
 *       {@code SMO_ENROLLMENT_SECRET[_FILE]} when it enrols and asks for the {@code smo-internal} scope;</li>
 *   <li>{@code MODULE}: a name for the invoker label (default {@code java-rapp}).</li>
 * </ul>
 *
 * mTLS (the Python client's {@code SMO_MTLS=on}) is not read from the environment: build the {@link java.net.http.HttpClient}
 * with the {@code SSLContext} you need and pass it to {@link R1Client}.
 */
public record SmoConfig(
        String gatewayUrl,
        String invokerId,
        String invokerSecret,
        String bootstrapKey,
        boolean module,
        String enrollmentSecret,
        String name,
        Duration requestTimeout,
        RetryPolicy retry) {

    public static final String DEFAULT_GATEWAY = "http://r1-termination:8000";
    public static final String RAPP_SCOPE = "smo-rapp";
    public static final String MODULE_SCOPE = "smo-internal";

    public SmoConfig {
        if (gatewayUrl == null || gatewayUrl.isBlank()) {
            throw new IllegalArgumentException("gatewayUrl is required");
        }
        gatewayUrl = gatewayUrl.replaceAll("/+$", "");
        if ((invokerId == null) != (invokerSecret == null)) {
            throw new IllegalArgumentException("invokerId and invokerSecret go together");
        }
        name = name == null || name.isBlank() ? "java-rapp" : name;
        requestTimeout = requestTimeout == null ? Duration.ofSeconds(30) : requestTimeout;
        retry = retry == null ? RetryPolicy.defaults() : retry;
    }

    /** Defaults for a rApp at {@code gatewayUrl} that enrols itself. */
    public static SmoConfig of(String gatewayUrl) {
        return new SmoConfig(gatewayUrl, null, null, null, false, null, null, null, null);
    }

    public static SmoConfig fromEnv() {
        return fromEnv(System::getenv);
    }

    /** {@link #fromEnv()} over any lookup (a test's map). */
    public static SmoConfig fromEnv(Function<String, String> env) {
        String id = blankToNull(env.apply("SMO_INVOKER_ID"));
        String secret = blankToNull(env.apply("SMO_INVOKER_SECRET"));
        boolean isModule = "module".equalsIgnoreCase(orDefault(env.apply("SMO_IDENTITY_KIND"), "rapp").trim());
        return new SmoConfig(
                orDefault(env.apply("R1_GATEWAY_URL"), DEFAULT_GATEWAY),
                id,
                id == null ? null : secret,
                secret(env, "SMO_BOOTSTRAP_KEY"),
                isModule,
                isModule ? secret(env, "SMO_ENROLLMENT_SECRET") : null,
                env.apply("MODULE"),
                null,
                null);
    }

    public SmoConfig withRetry(RetryPolicy policy) {
        return new SmoConfig(gatewayUrl, invokerId, invokerSecret, bootstrapKey, module, enrollmentSecret, name, requestTimeout, policy);
    }

    public SmoConfig withTimeout(Duration timeout) {
        return new SmoConfig(gatewayUrl, invokerId, invokerSecret, bootstrapKey, module, enrollmentSecret, name, timeout, retry);
    }

    public SmoConfig withIdentity(String id, String secret) {
        return new SmoConfig(gatewayUrl, id, secret, bootstrapKey, module, enrollmentSecret, name, requestTimeout, retry);
    }

    public SmoConfig withBootstrapKey(String key) {
        return new SmoConfig(gatewayUrl, invokerId, invokerSecret, key, module, enrollmentSecret, name, requestTimeout, retry);
    }

    public SmoConfig withName(String label) {
        return new SmoConfig(gatewayUrl, invokerId, invokerSecret, bootstrapKey, module, enrollmentSecret, label, requestTimeout, retry);
    }

    /** The OAuth scope this identity asks for. */
    public String scope() {
        return module ? MODULE_SCOPE : RAPP_SCOPE;
    }

    /** A secret from {@code NAME}, or from the file {@code NAME_FILE} names; null when neither is set. */
    private static String secret(Function<String, String> env, String name) {
        String file = blankToNull(env.apply(name + "_FILE"));
        if (file != null) {
            try {
                return blankToNull(Files.readString(Path.of(file), StandardCharsets.UTF_8).strip());
            } catch (IOException e) {
                throw new IllegalStateException("cannot read " + name + "_FILE (" + file + ")", e);
            }
        }
        return blankToNull(env.apply(name));
    }

    private static String orDefault(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }

    /** Never prints a secret. */
    @Override
    public String toString() {
        return "SmoConfig[gatewayUrl=" + gatewayUrl + ", invokerId=" + invokerId + ", module=" + module + ", name=" + name + "]";
    }
}
