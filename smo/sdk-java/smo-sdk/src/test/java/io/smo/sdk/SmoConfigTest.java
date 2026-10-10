package io.smo.sdk;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * Covers {@link SmoConfig}: the defaults and variable names it shares with the Python client, secret files, the module and rApp
 * identity kinds, and that no secret shows in {@code toString}. The environment is a map or lambda, so nothing is read from
 * the real environment. Run: cd smo/sdk-java && ./mvnw -B -ntp -C verify (JDK 21; CI job sdk-java).
 */
class SmoConfigTest {

    /**
     * Pins the defaults of an empty environment.
     */
    @Test
    void defaultsMatchThePythonClient() {
        SmoConfig c = SmoConfig.fromEnv(k -> null);
        assertEquals("http://r1-termination:8000", c.gatewayUrl());
        assertFalse(c.module());
        assertEquals("smo-rapp", c.scope());
        assertNull(c.invokerId());
        assertEquals("java-rapp", c.name());
    }

    /**
     * Pins the variable names, and that a trailing slash on the gateway URL is dropped.
     */
    @Test
    void readsTheSameVariablesAsThePythonClient() {
        Map<String, String> env = new HashMap<>(Map.of(
                "R1_GATEWAY_URL", "https://r1.example:8443/", "SMO_INVOKER_ID", "id-1", "SMO_INVOKER_SECRET", "sec-1",
                "SMO_BOOTSTRAP_KEY", "bk", "MODULE", "my-rapp"));
        SmoConfig c = SmoConfig.fromEnv(env::get);
        assertEquals("https://r1.example:8443", c.gatewayUrl(), "trailing slash dropped");
        assertEquals("id-1", c.invokerId());
        assertEquals("sec-1", c.invokerSecret());
        assertEquals("bk", c.bootstrapKey());
        assertEquals("my-rapp", c.name());
    }

    /**
     * Pins that NAME_FILE takes precedence over NAME, loses its trailing newline, and an unreadable file is an error.
     */
    @Test
    void aSecretFileWinsAndIsStripped(@TempDir Path dir) throws Exception {
        Path file = dir.resolve("bootstrap");
        Files.writeString(file, "from-file\n");
        Map<String, String> env = Map.of("SMO_BOOTSTRAP_KEY", "from-env", "SMO_BOOTSTRAP_KEY_FILE", file.toString());
        assertEquals("from-file", SmoConfig.fromEnv(env::get).bootstrapKey());
        assertThrows(IllegalStateException.class, () -> SmoConfig.fromEnv(Map.of("SMO_BOOTSTRAP_KEY_FILE", dir.resolve("missing").toString())::get));
    }

    /**
     * Pins that only the module identity reads the enrollment secret and asks for the internal scope.
     */
    @Test
    void aModuleReadsItsEnrollmentSecretARappDoesNot() {
        Map<String, String> env = Map.of("SMO_ENROLLMENT_SECRET", "e", "SMO_IDENTITY_KIND", "module");
        assertEquals("e", SmoConfig.fromEnv(env::get).enrollmentSecret());
        assertEquals("smo-internal", SmoConfig.fromEnv(env::get).scope());
        assertNull(SmoConfig.fromEnv(Map.of("SMO_ENROLLMENT_SECRET", "e")::get).enrollmentSecret());
    }

    /**
     * Pins that an invoker id without a secret is refused when built in code and a secret without an id is ignored from the environment.
     */
    @Test
    void anIdentityIsIdAndSecretTogether() {
        assertThrows(IllegalArgumentException.class, () -> SmoConfig.of("http://x").withIdentity("id", null));
        assertNull(SmoConfig.fromEnv(Map.of("SMO_INVOKER_SECRET", "orphan")::get).invokerSecret(), "a secret without an id is ignored");
    }

    /**
     * Pins that logging a config cannot leak the invoker secret or the bootstrap key.
     */
    @Test
    void toStringNeverContainsASecret() {
        SmoConfig c = SmoConfig.of("http://x").withIdentity("id", "TOPSECRET").withBootstrapKey("KEYSECRET");
        assertFalse(c.toString().contains("TOPSECRET"));
        assertFalse(c.toString().contains("KEYSECRET"));
        assertTrue(c.toString().contains("id"));
    }
}
