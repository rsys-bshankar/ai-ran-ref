# The Java example rApp (hello-rapp) as a container. Build context: smo/sdk-java
#   docker build -f examples/hello-rapp/Dockerfile -t hello-java-rapp:dev .
# Base images are pinned by digest (Dependabot's docker ecosystem updates them).

FROM eclipse-temurin:21-jdk-noble@sha256:b468c3fc688b14450571494f588bd939378e7fd542ed5a73f8efc13f17872a87 AS build
WORKDIR /src
COPY mvnw pom.xml dependencies.sha256 ./
COPY .mvn .mvn
COPY smo-sdk/pom.xml smo-sdk/pom.xml
COPY examples/hello-rapp/pom.xml examples/hello-rapp/pom.xml
COPY smo-sdk/src smo-sdk/src
COPY examples/hello-rapp/src examples/hello-rapp/src
# The SDK's contract test reads docs/openapi, which is outside this context: the image build skips tests (CI runs them).
RUN ./mvnw -B -ntp -C -q -DskipTests -pl examples/hello-rapp -am package

FROM eclipse-temurin:21-jre-noble@sha256:000fd431958bc81a24abe1e8e5f0f0fd3ae365a594bd50aadb20696805f9408c
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
