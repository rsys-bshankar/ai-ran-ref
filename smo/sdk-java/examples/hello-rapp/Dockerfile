# The Java example rApp (hello-rapp) as a container. Build context: smo/sdk-java
#   docker build -f examples/hello-rapp/Dockerfile -t hello-java-rapp:dev .
# Base images are pinned by digest (Dependabot's docker ecosystem updates them).

# The build stage is the Maven image, pinned by digest (Maven 3.9.11, the version the wrapper pins for local and CI builds): the image build
# downloads no Maven distribution, so there is no wrapper download to fail or to verify here.
FROM maven:3.9.11-eclipse-temurin-21-noble@sha256:6fdc855a6ed81d288ca7ca37ac6ff5e9308b612485c0801d70b25a858c83d237 AS build
WORKDIR /src
COPY pom.xml ./
COPY smo-sdk/pom.xml smo-sdk/pom.xml
COPY examples/hello-rapp/pom.xml examples/hello-rapp/pom.xml
COPY smo-sdk/src smo-sdk/src
COPY examples/hello-rapp/src examples/hello-rapp/src
# The SDK's contract test reads docs/openapi, which is outside this context: the image build skips tests (CI runs them).
RUN mvn -B -ntp -C -q -DskipTests -pl examples/hello-rapp -am package

FROM eclipse-temurin:25-jre-noble@sha256:d9a39a23634650173f1e2bbc176227af9728587ecf0f4b62d53e9355cd7a19ab
RUN groupadd --system --gid 10001 smo \
 && useradd --system --uid 10001 --gid smo --no-create-home --shell /usr/sbin/nologin smo
WORKDIR /srv/app
COPY --from=build /src/examples/hello-rapp/target/hello-rapp.jar hello-rapp.jar
COPY --from=build /src/examples/hello-rapp/target/lib lib
USER 10001:10001
ENV PORT=8000
EXPOSE 8000
# -XX:MaxRAMPercentage: size the heap from the container limit; the root file system is read-only in compose, the JVM needs /tmp only
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75", "-jar", "/srv/app/hello-rapp.jar"]
