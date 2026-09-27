FROM maven:3.9.9-eclipse-temurin-21@sha256:3a4ab3276a087bf276f79cae96b1af04f53731bec53fb2e651aca79e4b10211e AS build
WORKDIR /build
COPY music-backend/pom.xml ./
COPY music-backend/src/ src/
RUN mvn -B verify
COPY deploy/HttpHealthCheck.java ./
RUN javac -d /build/probes HttpHealthCheck.java

FROM eclipse-temurin:21-jre-jammy@sha256:e9aaf73145bbd1f9f6ec7f6867dd75a44f34b1a6c32a813504bf4129be2d09d7
RUN useradd --system --uid 10001 app
WORKDIR /app
COPY --from=build /build/target/music-backend-0.0.1-SNAPSHOT.jar app.jar
COPY --from=build /build/probes/HttpHealthCheck.class /app/HttpHealthCheck.class
USER app
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
# Existing no-login endpoint exercises MVC without accessing user cookies/upstream.
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 CMD java -cp /app HttpHealthCheck http://127.0.0.1:8080/api/auth/status
