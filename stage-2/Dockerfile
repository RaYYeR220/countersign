FROM golang:1.26-alpine AS build
WORKDIR /src
COPY . .
RUN CGO_ENABLED=0 GOFLAGS=-mod=vendor GOTOOLCHAIN=local \
    go build -trimpath -ldflags="-s -w" -o /out/tablekeeper .

FROM alpine:3.22
RUN adduser -D -H tablekeeper
COPY --from=build /out/tablekeeper /usr/local/bin/tablekeeper
USER tablekeeper
ENV PORT=8080
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/tablekeeper"]
