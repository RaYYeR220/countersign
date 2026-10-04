# Tablekeeper — stage 4

Build and start (from this folder):

```sh
docker build -t tablekeeper-stage4 . && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage4
```

The service listens on `0.0.0.0:$PORT` (default `8080`) and answers `GET /health` with
`200 {"status":"ok"}` once it is ready. No other setup is needed: all dependencies, the IANA
time-zone database and the browser UI's assets are compiled into the binary, and the image needs
no network at run time. State is held in memory; load data with `POST /_test/reset`.

The browser screens are at `/`, `/signup`, `/login` and `/lookup` (e.g. `http://127.0.0.1:8080/`).

Stage 3 added dated booking policies (`/restaurants/{id}/policies`), availability explanations
(`explain=true`), reservation revisions and accepted terms, history and decision records and
recurring series (`POST /series`, `GET /series/{id}`).

Stage 4 adds seating repairs after a table closure: a manager previews an optimal plan with
`POST /restaurants/{id}/replans` and applies it atomically with
`POST /restaurants/{id}/replans/{plan_id}/apply`. Applied closures then take the table out of
availability and bookings for their interval. Recurring series can be amended with
`POST /series/{series_id}/amend`. `POST /_test/import` accepts exports from the stage-1, stage-2
and stage-3 services as well as its own.

Unit tests (Go 1.26, from this folder): `go test ./...`
