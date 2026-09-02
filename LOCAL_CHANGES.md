# Local customizations — `claude_agent_pipe.py`

Changes maintained on top of upstream (`Speedliner/openwebui-claude-code`) for a
**uv-tool OpenWebUI deployment** (community-scripts ProxmoxVE installer) that also
serves this pipe to **Nextcloud Assistant** via the OpenAI-compatible API.

All edits are additive and wrapped in `# >>> local edit` / `# <<< local edit`
markers so they're obvious in diffs and during rebase conflict resolution.

| # | Change | Why |
|---|--------|-----|
| 1 | uv dependency bootstrap + remove `requirements:` line | uv-tool env has no `pip`; OpenWebUI's frontmatter pip install fails |
| 2 | `RENDER_THINKING` valve + `_render_thinking()` helper | `<details>` thinking renders as raw text in Nextcloud Assistant |
| 3 | `SHOW_COST` valve gate | cost/time footer is noise for API consumers |

> **Anchor note:** line-level formatting of the thinking-emit and cost-footer
> blocks can drift between upstream versions. Match the *block* in your current
> file, not the exact whitespace shown here.

---

## Change 1 — uv dependency bootstrap

**1a. Remove the `requirements:` line** from the frontmatter docstring at the top
of the file:

```diff
- requirements: claude-agent-sdk>=0.1.60, anthropic>=0.40.0
```

Leave it out entirely. If it's present, OpenWebUI tries to `pip install` it
*before* the module is imported — which fails in a uv-tool env (no pip) and the
function never loads, so the bootstrap below never gets a chance to run.

**1b. Add the bootstrap** immediately after the top import block (right after
`from pydantic import BaseModel, Field`), before the inlined session-store code:

```python
# >>> local edit: uv dependency bootstrap ---------------------------------
# OpenWebUI here is a `uv tool` install (isolated env, no pip), so the
# frontmatter `requirements:` pip path can't run — that line is intentionally
# removed above. Instead we install runtime deps into THIS interpreter's env
# via uv, on first import. Self-heals across `uv tool upgrade open-webui`
# (which wipes extra packages): the next load just reinstalls them.
import importlib.util
import shutil
import subprocess
import sys


def _ensure_deps() -> None:
    required = {
        "claude_agent_sdk": "claude-agent-sdk>=0.1.60",
        "anthropic": "anthropic>=0.40.0",
    }
    missing = [
        spec for mod, spec in required.items()
        if importlib.util.find_spec(mod) is None
    ]
    if not missing:
        return
    # systemd service PATH usually lacks ~/.local/bin, so find uv explicitly.
    uv = shutil.which("uv") or next(
        (p for p in (
            os.path.expanduser("~/.local/bin/uv"),
            "/root/.local/bin/uv",
            "/usr/local/bin/uv",
        ) if os.path.exists(p)),
        None,
    )
    if uv:
        cmd = [uv, "pip", "install", "--python", sys.executable, *missing]
    else:
        # Last resort if the uv binary isn't found.
        subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"], check=False)
        cmd = [sys.executable, "-m", "pip", "install", *missing]
    subprocess.check_call(cmd)


_ensure_deps()
# <<< local edit ----------------------------------------------------------
```

(`os` is already imported in the top block, so it's in scope here. `find_spec`
checks presence, not version — if you ever need to force a version bump, delete
the old package from the env manually.)

---

## Change 2 — `RENDER_THINKING` valve

**2a. Add the valve** inside `class Valves(BaseModel):` (alongside the other
fields):

```python
        # >>> local edit: output-rendering valves
        RENDER_THINKING: str = Field(
            default="details",
            description=(
                "How to render model thinking: 'details' (collapsible HTML, "
                "OpenWebUI UI), 'plain' (blockquote text), or 'off' (drop it). "
                "Use 'off' or 'plain' for OpenAI-API consumers like Nextcloud "
                "Assistant that don't render <details>."
            ),
        )
        SHOW_COST: bool = Field(
            default=True,
            description=(
                "Append the '_Cost: $... . ...ms_' footer after each reply. "
                "Turn off for OpenAI-API consumers where it's just noise."
            ),
        )
        # <<< local edit
```

(Both valves added together since they're the same concern.)

**2b. Add the helper method** on `class Pipe:` (e.g. right after `pipes()`):

```python
    # >>> local edit: thinking renderer
    def _render_thinking(self, text: str) -> str:
        text = text.strip()
        if not text or self.valves.RENDER_THINKING == "off":
            return ""
        if self.valves.RENDER_THINKING == "plain":
            quoted = "\n".join(f"> {ln}" for ln in text.splitlines())
            return f"\n\n> 💭 **Thinking**\n{quoted}\n\n"
        return (
            "\n\n<details>\n<summary>💭 Thinking</summary>\n\n"
            f"{text}\n\n</details>\n\n"
        )
    # <<< local edit
```

**2c. Route both emit sites through it.** There are two `content_block_stop`
handlers that emit thinking — one in `pipe()`, one in `_run_lite_agent()`. Each
looks roughly like:

```python
                        elif etype == "content_block_stop":
                            idx = ev.get("index", 0)
                            if idx in thinking_buffers:
                                text = thinking_buffers.pop(idx).strip()
                                if text:
                                    yield (
                                        "\n\n<details>\n<summary>💭 Thinking</summary>\n\n"
                                        f"{text}\n\n"
                                        "</details>\n\n"
                                    )
```

Replace the inner `text = ... / if text:` yield with:

```python
                        elif etype == "content_block_stop":
                            idx = ev.get("index", 0)
                            if idx in thinking_buffers:
                                # >>> local edit: route through _render_thinking
                                block = self._render_thinking(thinking_buffers.pop(idx))
                                if block:
                                    yield block
                                # <<< local edit
```

---

## Change 3 — `SHOW_COST` valve gate

The `SHOW_COST` field was added in Change 2a. Now gate the footer. In the
`ResultMessage` handler at the end of `pipe()`:

```python
                        if message.total_cost_usd is not None:
                            yield f"\n\n_Cost: ${message.total_cost_usd:.4f} · {message.duration_ms}ms_\n"
```

becomes:

```python
                        # >>> local edit: gate cost footer behind SHOW_COST
                        if message.total_cost_usd is not None and self.valves.SHOW_COST:
                            yield f"\n\n_Cost: ${message.total_cost_usd:.4f} · {message.duration_ms}ms_\n"
                        # <<< local edit
```

The adjacent `Agent stopped: {subtype}` line is deliberately left alone — it only
fires on a non-success result (max turns, error, interruption), so it's signal,
not noise.

---

## Recommended valve settings for the Nextcloud-facing model

In OpenWebUI → the function's valves (or the Workspace Model that Nextcloud
targets):

- `RENDER_THINKING = off` (or `plain` to keep reasoning as a blockquote)
- `SHOW_COST = false`

**Caveat:** valves are global to the function, so these also affect your
OpenWebUI browser UI. If you want `details` + cost in the browser but stripped in
Nextcloud, add a `UserValves` class to the pipe and override per-user
(`__user__["valves"]`), keyed to the OWUI user Nextcloud's API key authenticates
as. Otherwise pick the setting for whichever surface you use most.

---

## Committing as one unit

```bash
git checkout -b local
# apply the three changes above, plus save this file at repo root
git add claude_agent_pipe.py LOCAL_CHANGES.md
git commit -m "local: uv dep bootstrap, RENDER_THINKING + SHOW_COST valves"
git push origin local
```

Generate the portable patch from that commit if you want a standalone artifact:

```bash
git format-patch -1 HEAD --stdout > local-customizations.patch
```

## Pulling upstream changes later

```bash
git checkout main
git fetch upstream
git merge --ff-only upstream/main
git push origin main

git checkout local
git rebase main          # replays your one commit on top of his latest
# fix any conflicts in claude_agent_pipe.py (grep for "local edit"), then:
git push --force-with-lease origin local
```

If an upstream release restructures the file badly enough that the rebase gets
messy, abort it (`git rebase --abort`) and reapply from this doc by hand — the
markers make each edit a quick find-and-replace.

## Deploying

OpenWebUI doesn't pull from git. After updating `local`, copy the file contents
into the OpenWebUI function editor (or push via the
`/api/v1/functions/id/{id}/update` API). The fork gives you clean change-tracking
and painless merges; deploy stays a separate copy step.