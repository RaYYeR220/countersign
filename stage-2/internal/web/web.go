// Package web serves the browser product (stage 2): one HTML shell for every screen route and the
// embedded stylesheet and script. Nothing is fetched from elsewhere at run time — fonts are system
// stacks and every asset is compiled into the binary.
package web

import (
	"bytes"
	"embed"
	"io/fs"
	"net/http"
	"time"
)

//go:embed static
var static embed.FS

// Routes are the screen routes that return the HTML shell; the client renders the screen.
var Routes = []string{"/{$}", "/signup", "/login", "/lookup"}

var (
	shell   = mustRead("static/index.html")
	modTime = time.Now()
)

func mustRead(name string) []byte {
	b, err := static.ReadFile(name)
	if err != nil {
		panic(err)
	}
	return b
}

// Page serves the HTML shell.
func Page(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	http.ServeContent(w, r, "index.html", modTime, bytes.NewReader(shell))
}

var assetTypes = map[string]string{
	"app.js":  "text/javascript; charset=utf-8",
	"app.css": "text/css; charset=utf-8",
}

// Asset serves /assets/{name}; unknown names are reported through notFound.
func Asset(notFound http.HandlerFunc) http.HandlerFunc {
	sub, _ := fs.Sub(static, "static")
	return func(w http.ResponseWriter, r *http.Request) {
		name := r.PathValue("name")
		ctype, ok := assetTypes[name]
		if !ok {
			notFound(w, r)
			return
		}
		b, err := fs.ReadFile(sub, name)
		if err != nil {
			notFound(w, r)
			return
		}
		w.Header().Set("Content-Type", ctype)
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		http.ServeContent(w, r, name, modTime, bytes.NewReader(b))
	}
}
