// seat-launcher runs a seat's coding-agent CLI with a pinned, clean profile so the seat
// never loads the operator's personal configuration. Arguments and stdio pass through
// unchanged.
//
// Configuration lives next to the executable:
//   - launcher.json: {"target": "<path to the real CLI>", "env": {"NAME": "value"}}.
//     The token {dir} in any value is replaced by the launcher's own directory.
//   - otherwise claude-path.txt (or "claude" on PATH) is run with CLAUDE_CONFIG_DIR set
//     to the launcher's directory.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

type config struct {
	Target string            `json:"target"`
	Env    map[string]string `json:"env"`
	Log    string            `json:"log"`
}

func load(dir string) (string, []string, string, error) {
	if b, err := os.ReadFile(filepath.Join(dir, "launcher.json")); err == nil {
		var c config
		if err := json.Unmarshal(b, &c); err != nil {
			return "", nil, "", fmt.Errorf("launcher.json: %w", err)
		}
		if c.Target == "" {
			return "", nil, "", fmt.Errorf("launcher.json: target is empty")
		}
		env := []string{}
		for k, v := range c.Env {
			env = append(env, k+"="+strings.ReplaceAll(v, "{dir}", dir))
		}
		return strings.ReplaceAll(c.Target, "{dir}", dir), env, strings.ReplaceAll(c.Log, "{dir}", dir), nil
	}
	target := "claude"
	if b, err := os.ReadFile(filepath.Join(dir, "claude-path.txt")); err == nil {
		if p := strings.TrimSpace(string(b)); p != "" {
			target = p
		}
	}
	return target, []string{"CLAUDE_CONFIG_DIR=" + dir}, "", nil
}

func main() {
	self, err := os.Executable()
	if err != nil {
		fmt.Fprintln(os.Stderr, "seat-launcher:", err)
		os.Exit(1)
	}
	target, env, logPath, err := load(filepath.Dir(self))
	if err != nil {
		fmt.Fprintln(os.Stderr, "seat-launcher:", err)
		os.Exit(1)
	}
	cmd := exec.Command(target, os.Args[1:]...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	cmd.Env = append(os.Environ(), env...)
	if logPath != "" {
		// Diagnostics only: record how the seat was started and what the CLI said on stderr.
		if f, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600); err == nil {
			defer f.Close()
			names := []string{}
			for _, kv := range cmd.Env {
				name := strings.SplitN(kv, "=", 2)[0]
				up := strings.ToUpper(name)
				if strings.HasPrefix(up, "XDG_") || strings.HasPrefix(up, "OPENCODE") || strings.HasPrefix(up, "JAM_") {
					names = append(names, kv)
				} else {
					names = append(names, name)
				}
			}
			wd, _ := os.Getwd()
			fmt.Fprintf(f, "start target=%s args=%q cwd=%s env=%v\n", target, os.Args[1:], wd, names)
			cmd.Stderr = io.MultiWriter(os.Stderr, f)
		}
	}
	if err := cmd.Run(); err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			os.Exit(ee.ExitCode())
		}
		fmt.Fprintln(os.Stderr, "seat-launcher:", err)
		os.Exit(1)
	}
}
