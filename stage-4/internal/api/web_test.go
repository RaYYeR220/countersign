package api

import (
	"net/http"
	"strings"
	"testing"
)

func TestScreenRoutesServeHTML(t *testing.T) {
	_, h := newTestServer()
	for _, path := range []string{"/", "/signup", "/login", "/lookup", "/?restaurant=r_a"} {
		rec := do(h, http.MethodGet, path, "")
		if rec.Code != 200 || rec.Header().Get("Content-Type") != "text/html; charset=utf-8" ||
			!strings.Contains(rec.Body.String(), `<script src="/assets/app.js"`) {
			t.Errorf("GET %s = %d %q", path, rec.Code, rec.Header().Get("Content-Type"))
		}
	}
	for path, ctype := range map[string]string{"/assets/app.js": "text/javascript; charset=utf-8", "/assets/app.css": "text/css; charset=utf-8"} {
		if rec := do(h, http.MethodGet, path, ""); rec.Code != 200 || rec.Header().Get("Content-Type") != ctype || rec.Body.Len() == 0 {
			t.Errorf("GET %s = %d %q", path, rec.Code, rec.Header().Get("Content-Type"))
		}
	}
	for _, path := range []string{"/assets/nope.js", "/assets/index.html", "/lookup/x", "/signup/", "/nope"} {
		if rec := do(h, http.MethodGet, path, ""); rec.Code != 404 || errorCode(t, rec) != "not_found" {
			t.Errorf("GET %s = %d %s", path, rec.Code, rec.Body)
		}
	}
	if rec := do(h, http.MethodPost, "/", ""); rec.Code != 405 {
		t.Errorf("POST / = %d", rec.Code)
	}
	// No asset is fetched from another origin at run time.
	for _, path := range []string{"/", "/assets/app.css", "/assets/app.js"} {
		body := do(h, http.MethodGet, path, "").Body.String()
		if strings.Contains(body, "http://") && !strings.Contains(body, "http://www.w3.org/2000/svg") || strings.Contains(body, "https://") || strings.Contains(body, "@import") {
			t.Errorf("%s references an external resource", path)
		}
	}
}
