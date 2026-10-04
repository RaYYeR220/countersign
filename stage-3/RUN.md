# Tablekeeper — stage 2

Build and start (from this folder):

```sh
docker build -t tablekeeper-stage2 . && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage2
```

The service listens on `0.0.0.0:$PORT` (default `8080`) and answers `GET /health` with
`200 {"status":"ok"}` once it is ready. No other setup is needed: all dependencies, the IANA
time-zone database and the browser UI's assets are compiled into the binary, and the image needs
no network at run time. State is held in memory; load data with `POST /_test/reset`.

The browser screens are at `/`, `/signup`, `/login` and `/lookup` (e.g. `http://127.0.0.1:8080/`).
`POST /_test/import` also accepts an export from the stage-1 service.

Unit tests (Go 1.26, from this folder): `go test ./...`
