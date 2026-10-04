# Tablekeeper — stage 1

Build and start (from this folder):

```sh
docker build -t tablekeeper-stage1 . && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage1
```

The service listens on `0.0.0.0:$PORT` (default `8080`) and answers `GET /health` with
`200 {"status":"ok"}` once it is ready. No other setup is needed: all dependencies, including the
IANA time-zone database, are compiled into the binary, and the image needs no network at run time.
State is held in memory; load data with `POST /_test/reset`.

Unit tests (Go 1.26, from this folder): `go test ./...`
