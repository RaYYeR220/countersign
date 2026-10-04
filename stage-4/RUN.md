# Tablekeeper — stage 3

Build and start (from this folder):

```sh
docker build -t tablekeeper-stage3 . && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage3
```

The service listens on `0.0.0.0:$PORT` (default `8080`) and answers `GET /health` with
`200 {"status":"ok"}` once it is ready. No other setup is needed: all dependencies, the IANA
time-zone database and the browser UI's assets are compiled into the binary, and the image needs
no network at run time. State is held in memory; load data with `POST /_test/reset`.

The browser screens are at `/`, `/signup`, `/login` and `/lookup` (e.g. `http://127.0.0.1:8080/`).

Stage 3 adds dated booking policies (`/restaurants/{id}/policies`, published by the restaurant's
`manager_user_ids`), availability explanations (`GET /availability?…&explain=true`), reservation
revisions and accepted terms, history and decision records (`/reservations/{reference}/history`,
`/reservations/{reference}/decision`) and recurring series (`POST /series`, `GET /series/{id}`).
`POST /_test/import` accepts exports from the stage-1 and stage-2 services as well as its own.

Unit tests (Go 1.26, from this folder): `go test ./...`
