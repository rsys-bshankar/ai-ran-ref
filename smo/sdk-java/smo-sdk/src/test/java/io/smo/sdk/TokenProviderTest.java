package io.smo.sdk;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.net.http.HttpClient;
import java.time.Duration;
import org.junit.jupiter.api.Test;

class TokenProviderTest {

    private static TokenProvider provider(SmoConfig config, FakePlatform.TestClock clock) {
        HttpCaller http = new HttpCaller(HttpClient.newHttpClient(), Duration.ofSeconds(5), d -> { });
        return new TokenProvider(config, http, clock);
    }

    private static SmoConfig config(FakePlatform fake) {
        return SmoConfig.of(fake.url()).withRetry(RetryPolicy.none()).withName("unit");
    }

    @Test
    void enrolsThenGrantsARappToken() throws Exception {
        try (FakePlatform fake = new FakePlatform().withSme("tok-1", 300)) {
            TokenProvider tokens = provider(config(fake).withBootstrapKey("bk"), new FakePlatform.TestClock());

            assertEquals("tok-1", tokens.token());
            assertEquals("inv-1", tokens.invokerId());

            assertEquals("bk", fake.requests("GET", "/bootstrap").get(0).header("X-Bootstrap-Key"));
            FakePlatform.Seen enrol = fake.requests("POST", "/sme/invoker-registrations").get(0);
            JsonNode enrolBody = Json.MAPPER.readTree(enrol.body());
            assertTrue(enrolBody.get("apiInvokerPublicKey").asText().startsWith("smo-rapp:unit:"));
            assertNull(enrol.header("X-SMO-Enrollment"), "a rApp presents no enrollment secret");
            JsonNode grant = Json.MAPPER.readTree(fake.requests("POST", "/sme/oauth2/token").get(0).body());
            assertEquals("client_credentials", grant.get("grant_type").asText());
            assertEquals("inv-1", grant.get("client_id").asText());
            assertEquals("s3cret", grant.get("client_secret").asText());
            assertEquals("smo-rapp", grant.get("scope").asText());
        }
    }

    @Test
    void aModuleAsksForTheInternalScopeAndPresentsItsEnrollmentSecret() throws Exception {
        try (FakePlatform fake = new FakePlatform().withSme("tok", 300)) {
            SmoConfig module = SmoConfig.fromEnv(k -> switch (k) {
                case "R1_GATEWAY_URL" -> fake.url();
                case "SMO_IDENTITY_KIND" -> "module";
                case "SMO_ENROLLMENT_SECRET" -> "enroll-me";
                default -> null;
            }).withRetry(RetryPolicy.none());
            provider(module, new FakePlatform.TestClock()).token();

            assertEquals("enroll-me", fake.requests("POST", "/sme/invoker-registrations").get(0).header("X-SMO-Enrollment"));
            assertEquals("smo-internal", Json.MAPPER.readTree(fake.requests("POST", "/sme/oauth2/token").get(0).body()).get("scope").asText());
        }
    }

    @Test
    void cachesUntilThirtySecondsBeforeExpiryThenGrantsAgain() throws Exception {
        try (FakePlatform fake = new FakePlatform().withSme("tok-a", 100)) {
            fake.on("POST /sme/oauth2/token", 200, "{\"access_token\":\"tok-b\",\"expires_in\":100}");
            FakePlatform.TestClock clock = new FakePlatform.TestClock();
            TokenProvider tokens = provider(config(fake), clock);

            assertEquals("tok-a", tokens.token());
            clock.advance(Duration.ofSeconds(69));
            assertEquals("tok-a", tokens.token(), "still inside lifetime minus the 30 s margin");
            assertEquals(1, fake.requests("POST", "/sme/oauth2/token").size());

            clock.advance(Duration.ofSeconds(2));
            assertEquals("tok-b", tokens.token(), "past the margin: a new grant");
            assertEquals(2, fake.requests("POST", "/sme/oauth2/token").size());
            assertEquals(1, fake.requests("POST", "/sme/invoker-registrations").size(), "the invoker is enrolled once");
            assertEquals(1, fake.requests("GET", "/bootstrap").size(), "the token endpoint is discovered once");
        }
    }

    @Test
    void refreshDiscardsTheCachedToken() throws Exception {
        try (FakePlatform fake = new FakePlatform().withSme("tok-a", 3600)) {
            fake.on("POST /sme/oauth2/token", 200, "{\"access_token\":\"tok-b\",\"expires_in\":3600}");
            TokenProvider tokens = provider(config(fake), new FakePlatform.TestClock());
            assertEquals("tok-a", tokens.token());
            assertEquals("tok-b", tokens.token(true));
            assertEquals("tok-b", tokens.token());
        }
    }

    @Test
    void aPinnedIdentityIsNeverEnrolledOrOffboarded() throws Exception {
        try (FakePlatform fake = new FakePlatform().withSme("tok", 300)) {
            TokenProvider tokens = provider(config(fake).withIdentity("issued-id", "issued-secret"), new FakePlatform.TestClock());
            tokens.token();

            assertTrue(fake.requests("POST", "/sme/invoker-registrations").isEmpty());
            assertEquals("issued-id", Json.MAPPER.readTree(fake.requests("POST", "/sme/oauth2/token").get(0).body()).get("client_id").asText());
            assertFalse(tokens.offboard());
            assertTrue(fake.requests("DELETE", "/sme/invoker-registrations/issued-id").isEmpty());
        }
    }

    @Test
    void aForgottenSelfEnrolledInvokerIsEnrolledAfreshOnce() throws Exception {
        try (FakePlatform fake = new FakePlatform()) {
            fake.on("GET /bootstrap", 200, "{\"apiEndpoints\":[{\"tokenEndPoint\":{\"uri\":\"" + fake.url() + "/sme/oauth2/token\"}}]}")
                    .on("POST /sme/invoker-registrations", 201, "{\"apiInvokerId\":\"old\",\"onboardingSecret\":\"o\"}")
                    .on("POST /sme/invoker-registrations", 201, "{\"apiInvokerId\":\"new\",\"onboardingSecret\":\"n\"}")
                    .on("POST /sme/oauth2/token", 200, "{\"access_token\":\"t1\",\"expires_in\":31}")
                    .on("POST /sme/oauth2/token", 400, "{\"error\":\"invalid_client\",\"error_description\":\"invoker not registered\"}")
                    .on("POST /sme/oauth2/token", 200, "{\"access_token\":\"t2\",\"expires_in\":300}");
            FakePlatform.TestClock clock = new FakePlatform.TestClock();
            TokenProvider tokens = provider(config(fake), clock);

            assertEquals("t1", tokens.token());
            clock.advance(Duration.ofSeconds(5));          // lifetime 31 s minus the margin leaves 1 s
            assertEquals("t2", tokens.token());
            assertEquals("new", tokens.invokerId());
            assertEquals(2, fake.requests("POST", "/sme/invoker-registrations").size());
        }
    }

    @Test
    void aPinnedIdentitySmeRefusesIsAnErrorNotAReplacement() throws Exception {
        try (FakePlatform fake = new FakePlatform().withSme("tok", 300)) {
            fake.on("POST /sme/oauth2/token", 400, "{\"error\":\"invalid_client\"}");
            TokenProvider tokens = provider(config(fake).withIdentity("issued-id", "bad"), new FakePlatform.TestClock());
            // the scripted queue answers the 200 first (withSme); drain it
            tokens.token();
            SdkException e = assertThrows(SdkException.class, () -> tokens.token(true));
            assertEquals(400, e.status());
            assertTrue(fake.requests("POST", "/sme/invoker-registrations").isEmpty());
        }
    }

    @Test
    void aGrantSmeRefusesIsReportedWithItsStatusAndBody() throws Exception {
        try (FakePlatform fake = new FakePlatform()) {
            fake.on("GET /bootstrap", 200, "{\"apiEndpoints\":[{\"tokenEndPoint\":{\"uri\":\"" + fake.url() + "/sme/oauth2/token\"}}]}")
                    .on("POST /sme/invoker-registrations", 201, "{\"apiInvokerId\":\"i\",\"onboardingSecret\":\"s\"}")
                    .on("POST /sme/oauth2/token", 400, "{\"error\":\"invalid_scope\"}");
            TokenProvider tokens = provider(config(fake), new FakePlatform.TestClock());
            SdkException e = assertThrows(SdkException.class, tokens::token);
            assertEquals(400, e.status());
            assertTrue(e.body().contains("invalid_scope"));
        }
    }

    @Test
    void aBootstrapThatNamesNoTokenEndpointIsAnError() throws Exception {
        try (FakePlatform fake = new FakePlatform()) {
            fake.on("GET /bootstrap", 200, "{\"apiEndpoints\":[{\"apiName\":\"x\"}]}");
            assertThrows(SdkException.class, () -> provider(config(fake), new FakePlatform.TestClock()).token());
        }
    }

    @Test
    void transientErrorsDuringEnrolmentAreRetried() throws Exception {
        try (FakePlatform fake = new FakePlatform()) {
            fake.on("GET /bootstrap", 503, "{}")
                    .on("GET /bootstrap", 200, "{\"apiEndpoints\":[{\"tokenEndPoint\":{\"uri\":\"" + fake.url() + "/sme/oauth2/token\"}}]}")
                    .on("POST /sme/invoker-registrations", 201, "{\"apiInvokerId\":\"i\",\"onboardingSecret\":\"s\"}")
                    .on("POST /sme/oauth2/token", 200, "{\"access_token\":\"ok\",\"expires_in\":300}");
            SmoConfig retrying = SmoConfig.of(fake.url()).withRetry(new RetryPolicy(3, Duration.ofMillis(1), Duration.ofMillis(1), false, RetryPolicy.defaults().retryableStatuses()));
            assertEquals("ok", provider(retrying, new FakePlatform.TestClock()).token());
            assertEquals(2, fake.requests("GET", "/bootstrap").size());
        }
    }

    @Test
    void offboardDeregistersASelfEnrolledInvoker() throws Exception {
        try (FakePlatform fake = new FakePlatform().withSme("tok", 300)) {
            fake.on("DELETE /sme/invoker-registrations/inv-1", 204, null);
            TokenProvider tokens = provider(config(fake), new FakePlatform.TestClock());
            tokens.token();

            assertTrue(tokens.offboard());
            assertEquals("Bearer tok", fake.requests("DELETE", "/sme/invoker-registrations/inv-1").get(0).header("Authorization"));
            assertNull(tokens.invokerId());
            assertFalse(tokens.offboard(), "nothing left to remove");
        }
    }
}
