package io.smo.sdk;

import java.net.http.HttpClient;

/**
 * The entry point, the counterpart of the Python SDK's {@code AiRuntimeSdk}: namespace clients that share one
 * {@link R1Client}, so one SME invoker and one cached token per process.
 *
 * <pre>{@code
 * try (SmoSdk sdk = SmoSdk.fromEnv()) {
 *     JsonNode types = sdk.data().listTypes(null);
 * }
 * }</pre>
 *
 * Closing it deregisters an invoker the SDK enrolled for itself (a pinned identity is left alone).
 */
public final class SmoSdk implements AutoCloseable {
    private final R1Client r1;
    private final DataClient data;
    private final ModelsClient models;
    private final PlatformClient platform;
    private final InstancesClient instances;

    /**
     * Builds the namespace clients on {@code r1}, which they share; closing this object closes {@code r1}.
     */
    public SmoSdk(R1Client r1) {
        this.r1 = r1;
        this.data = new DataClient(r1);
        this.models = new ModelsClient(r1);
        this.platform = new PlatformClient(r1);
        this.instances = new InstancesClient(r1);
    }

    /**
     * As {@link #SmoSdk(R1Client)} with a new {@link R1Client} for {@code config}.
     */
    public SmoSdk(SmoConfig config) {
        this(new R1Client(config));
    }

    /**
     * As {@link #SmoSdk(SmoConfig)} with your own {@link HttpClient} (for mutual TLS, a proxy or another executor).
     */
    public SmoSdk(SmoConfig config, HttpClient httpClient) {
        this(new R1Client(config, httpClient));
    }

    /**
     * Reads {@link SmoConfig#fromEnv()} from the process environment and builds the SDK on it.
     */
    public static SmoSdk fromEnv() {
        return new SmoSdk(SmoConfig.fromEnv());
    }

    public R1Client r1() {
        return r1;
    }

    public DataClient data() {
        return data;
    }

    public ModelsClient models() {
        return models;
    }

    public PlatformClient platform() {
        return platform;
    }

    public InstancesClient instances() {
        return instances;
    }

    @Override
    public void close() {
        r1.close();
    }
}
