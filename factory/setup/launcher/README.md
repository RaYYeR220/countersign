# seat-launcher

Runs a seat's coding-agent CLI with a pinned, clean profile, so a seat never loads the
operator's personal configuration (instructions, hooks, plugins, MCP servers).

Build once and place the binary in a dedicated profile directory per runtime:

    go build -o <profile-dir>/seat-launcher.exe .

Claude Code: put `claude-path.txt` (absolute path of the real `claude` executable) next to
the binary; the launcher sets `CLAUDE_CONFIG_DIR` to that directory. Log in once with
`CLAUDE_CONFIG_DIR=<profile-dir> claude` and `/login`.

Any other CLI: put `launcher.json` next to the binary:

    {"target": "C:/path/to/opencode.exe",
     "env": {"OPENCODE_DISABLE_CLAUDE_CODE": "1", "XDG_CONFIG_HOME": "{dir}/xdg"}}

`{dir}` expands to the launcher's directory. Point the seat's spawn command at the binary
(an OpenCode seat also needs the spawn argument `acp`). Add `"log": "{dir}/launcher.log"` to
record how the runtime started the seat and what the CLI printed on stderr.
