# Runs giddygiddyboy's MIT-licensed Mech Royale bot from giddy/ (see giddy/LICENSE and giddy/README.md).
# Only change: the bot name in `ready` is NoisyBoy. The old Python bot stays in the repo but is not in this image.
FROM golang:1.26.1-bookworm@sha256:ab3d6955bbc813a0f3fdf220c1d817dd89c0b3f283777db8ece4a32fe7858edd AS build
WORKDIR /src
COPY giddy/go.mod ./
COPY giddy/mech ./mech
COPY giddy/cmd/bot ./cmd/bot
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags="-s -w" -o /mechbot ./cmd/bot

FROM scratch
COPY --from=build /mechbot /mechbot
ENTRYPOINT ["/mechbot"]
