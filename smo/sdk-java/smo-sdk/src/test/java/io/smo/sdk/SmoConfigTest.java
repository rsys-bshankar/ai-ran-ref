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

class SmoConfigTest {

    @Test
    void defaultsMatchThePythonClient() {
        SmoConfig c = SmoConfig.fromEnv(k -> null);
        assertEquals("http://r1-termination:8000", c.gatewayUrl());
        assertFalse(c.module());
        assertEquals("smo-rapp", c.scope());
        assertNull(c.invokerId());
        assertEquals("java-rapp", c.name());
    }

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

    @Test
    void aSecretFileWinsAndIsStripped(@TempDir Path dir) throws Exception {
        Path file = dir.resolve("bootstrap");
        Files.writeString(file, "from-file\n");
        Map<String, String> env = Map.of("SMO_BOOTSTRAP_KEY", "from-env", "SMO_BOOTSTRAP_KEY_FILE", file.toString());
        assertEquals("from-file", SmoConfig.fromEnv(env::get).bootstrapKey());
        assertThrows(IllegalStateException.class, () -> SmoConfig.fromEnv(Map.of("SMO_BOOTSTRAP_KEY_FILE", dir.resolve("missing").toString())::get));
    }

    @Test
    void aModuleReadsItsEnrollmentSecretARappDoesNot() {
        Map<String, String> env = Map.of("SMO_ENROLLMENT_SECRET", "e", "SMO_IDENTITY_KIND", "module");
        assertEquals("e", SmoConfig.fromEnv(env::get).enrollmentSecret());
        assertEquals("smo-internal", SmoConfig.fromEnv(env::get).scope());
        assertNull(SmoConfig.fromEnv(Map.of("SMO_ENROLLMENT_SECRET", "e")::get).enrollmentSecret());
    }

    @Test
    void anIdentityIsIdAndSecretTogether() {
        assertThrows(IllegalArgumentException.class, () -> SmoConfig.of("http://x").withIdentity("id", null));
        assertNull(SmoConfig.fromEnv(Map.of("SMO_INVOKER_SECRET", "orphan")::get).invokerSecret(), "a secret without an id is ignored");
    }

    @Test
    void toStringNeverContainsASecret() {
        SmoConfig c = SmoConfig.of("http://x").withIdentity("id", "TOPSECRET").withBootstrapKey("KEYSECRET");
        assertFalse(c.toString().contains("TOPSECRET"));
        assertFalse(c.toString().contains("KEYSECRET"));
        assertTrue(c.toString().contains("id"));
    }
}
