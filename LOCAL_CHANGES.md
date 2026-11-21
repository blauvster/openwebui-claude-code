# Local changes (this deployment)

The repo file is now identical to what runs live in OpenWebUI (`claude_code` function).
Differences from upstream, all marked `# >>> local edit` in the code:

- **No `requirements:` frontmatter + `_ensure_deps()`**: OpenWebUI here is a `uv tool` install without pip; deps are installed via `uv pip install` on first import.
- **`RENDER_THINKING` valve** (`details` | `plain` | `off`) for API consumers such as Nextcloud Assistant.
- **`SHOW_COST` valve** to hide the cost footer.
- **Multi-session routing** (`ROUTING_SERVICES` valve) — see README.

Deploy: update the `function` row (id `claude_code`) in `/root/.open-webui/webui.db`, then restart `open-webui`.
