# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM golang:1.27.2-alpine@sha256:85dc1069ac644ea3c527b177303a406eb3358192816cd7f9e5848eb658851673 AS build
ARG TARGETOS
ARG TARGETARCH
ARG VERSION=dev
ARG COMMIT=unknown
ARG BUILD_DATE=unknown
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH GOAMD64=v1 \
    go build -trimpath -buildvcs=false \
    -ldflags="-s -w -X main.version=$VERSION -X main.commit=$COMMIT -X main.date=$BUILD_DATE" \
    -o /out/ottplay-control-server ./cmd/ottplay-control-server

FROM scratch
COPY --from=build /out/ottplay-control-server /ottplay-control-server
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
USER 65532:65532
EXPOSE 8081
HEALTHCHECK --interval=30s --timeout=6s --start-period=5s --retries=3 CMD ["/ottplay-control-server", "healthcheck", "--url", "http://127.0.0.1:8081/healthz"]
ENTRYPOINT ["/ottplay-control-server"]
CMD ["serve", "--config", "/etc/ottplay-control-server/config.json", "--listen", "0.0.0.0:8081"]
