# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM golang:1.27.1-alpine@sha256:8a5910f31396cd4d89662f56c68b3ae31d374308270a1c3bd96672ee5ed43414 AS build
ARG TARGETOS
ARG TARGETARCH
ARG VERSION=dev
ARG COMMIT=unknown
ARG BUILD_DATE=unknown
WORKDIR /src
COPY go.mod ./
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
