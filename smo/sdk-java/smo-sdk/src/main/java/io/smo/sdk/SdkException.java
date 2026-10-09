package io.smo.sdk;

/**
 * Something went wrong talking to the platform. {@link #status()} is the HTTP status of the answer, or 0 when there
 * was none (a transport failure, or no access token could be obtained). The Python SDK's {@code SdkError} carries
 * the same two things: the status code and the body.
 */
public class SdkException extends RuntimeException {
    private static final long serialVersionUID = 1L;

    private final int status;
    private final String body;

    /**
     * An answer with an error status: {@link #status()} and {@link #body()} are what the platform sent.
     */
    public SdkException(int status, String body) {
        super(status + ": " + body);
        this.status = status;
        this.body = body;
    }

    /**
     * A failure with no answer (the transport failed, or the thread was interrupted): status 0, no body.
     */
    public SdkException(String message, Throwable cause) {
        super(message, cause);
        this.status = 0;
        this.body = null;
    }

    /**
     * A failure of the SDK itself, for instance a token answer without an access token: status 0, no body.
     */
    public SdkException(String message) {
        super(message);
        this.status = 0;
        this.body = null;
    }

    /** The HTTP status of the answer, 0 if there was none. */
    public int status() {
        return status;
    }

    /** The raw answer body (a ProblemDetails or an RFC 6749 error as JSON text, usually); null if there was no answer. */
    public String body() {
        return body;
    }

    /** True for a 4xx answer: the platform refused the request, repeating it unchanged will not help. */
    public boolean isClientError() {
        return status >= 400 && status < 500;
    }
}
