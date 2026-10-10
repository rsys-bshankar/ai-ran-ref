# The example rApp as a static binary in an empty image. Build from sdk-go/ (the module root):
#
#     docker build -f examples/hello-rapp/Dockerfile -t hello-go-rapp .
#
# The builder image is pinned by digest (golang:1.27.0-alpine; refresh with the registry's manifest digest when the Go
# version in go.mod or the .github/workflows/smo-tests.yml setup-go version moves). No dependency is downloaded: the module has none.
FROM golang:1.27.0-alpine@sha256:4c9fe60190a2a3350ddc51de80d0224b8a6698d12bdfc999fee45ea9d6c46dbc AS build
WORKDIR /src
COPY go.mod ./
COPY *.go ./
COPY examples ./examples
RUN CGO_ENABLED=0 GOFLAGS=-mod=readonly go build -trimpath -ldflags="-s -w" -o /out/hello-rapp ./examples/hello-rapp

# Nothing else is needed at run time: no shell, no package manager, no CA bundle (mutual TLS reads its CA from SMO_MTLS_CA_FILE).
FROM scratch
COPY --from=build /out/hello-rapp /hello-rapp
USER 65532:65532
EXPOSE 8000
# the image has no shell or curl: the binary probes itself
HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=10s CMD ["/hello-rapp", "-probe"]
ENTRYPOINT ["/hello-rapp"]
