oh this is fun — yes, hooks are absolutely where Claude Code shines. Your SessionStart idea is spot‑on for making the Memory Palace “just there” every time you open a session. I’ll give you (1) a short list of “must‑have” hooks, (2) a tightened SessionStart you can ship today (using endpoints you already have), (3) a few other high‑leverage hook patterns with tiny scripts, and (4) what to do first re: the JSON DSL vs the hook.

A couple of quick facts from the Claude Code hooks reference you shared (so we wire things correctly):

* For **SessionStart** and **UserPromptSubmit**, anything you print to **stdout** on exit code 0 is inserted into the conversation context automatically.
* **PreToolUse**/**PostToolUse** can be targeted with matchers like `Bash`, `Edit`, `Write`, etc. You can **deny/allow/ask** using JSON output with `hookSpecificOutput.permissionDecision`.
* Exit code **2** blocks (and feeds `stderr` back to Claude).

---

# No‑brainer hooks (worth it on every project)

### 1) SessionStart — boot your “context primer”

* Load top‑of‑mind Memory Palace bits (recent, high‑salience memories; active topics if available).
* Print the primer text to stdout. That’s enough; JSON is optional.
* Gracefully degrade if the service is down (don’t block).

### 2) PreToolUse (Bash: `git commit`) — quality gate before committing

* On `git commit`, run: `ruff check`, `pyright` (or `mypy`), and a quick `pytest -q` (or changed tests only).
* If anything fails, **deny** the tool call and surface the error summary. If clean, **allow**.
* Keeps commits green without nagging during normal editing.

### 3) UserPromptSubmit — inject “coding contract” & pet peeves (and optionally gate)

* Inject your short “team contract” (naming, docstrings, error‑handling, printing rules, etc.).
* Optionally detect risky intents (“rewrite entire repo”, “nuke files”, “bump dependency without tests”) and **ask** for confirmation.

### 4) Stop — gentle commit nudger (time/size based)

* When the assistant finishes a response, if it’s been > N minutes or > M files since last commit, show a one‑liner reminder (don’t block).
* Nice counterpart to #2: it nudges; #2 enforces only at commit time.

### 5) PreToolUse (Bash) — destructive-command guardrails

* Deny patterns like `rm -rf *`, `sudo`, `docker system prune -a`, writing outside the project, or sending large file globs to remote endpoints.
* Keep the rule list tiny and explicit; show a short reason when denied.

### 6) PreCompact (or Stop) — auto‑checkpoint summaries into Memory Palace

* When Claude compacts or ends a turn, post a tiny summary (and TODOs) to `/api/v1/memory/remember`.
* You get durable breadcrumbs across sessions.

### 7) PostToolUse (Write/Edit) — quick, quiet hygiene

* After file writes, run **formatting** only (e.g., `ruff format`) and do not block. Errors get surfaced at commit time by #2 anyway.

(You can do Notification hooks too for desktop pings, but IMO the seven above pull most of the weight without noise.)

---

# Your SessionStart hook — ship today (no JSON DSL required yet)

You can call **existing endpoints** now. In your repo today you’ve got:

* `/api/v1/memory/recall` with `min_salience`, `k` and default timestamp ordering (so you can get “recent & important” directly).
* `/api/v1/memory/health` exists; if you want richer “stats” (`friend_name`, counts, avg salience), we can add a tiny `/api/v1/memory/stats` later.

Here’s a lighter version that uses only what exists and degrades well:

```python
#!/usr/bin/env python3
# ~/.claude/hooks/memory_palace_startup.py

import json, sys, asyncio, httpx
from datetime import datetime

BASE = "http://localhost:8000/api/v1"

async def fetch_recent_important(client):
    # Uses /memory/recall (sorts by timestamp desc when no similarity)
    r = await client.post(f"{BASE}/memory/recall", json={
        "query": "",            # no vector search; just recency
        "k": 5,
        "threshold": 0.7,       # ignored when query="", fine to send
        "min_salience": 0.8
    })
    r.raise_for_status()
    data = r.json()
    return data.get("messages", [])

def fmt_memories(items):
    if not items:
        return "  (No recent high‑salience memories)"
    lines = []
    for m in items[:5]:
        ts = m.get("timestamp","")[:10]
        content = (m.get("content","") or "")[:120].replace("\n"," ")
        role = m.get("role","")
        lines.append(f"  • [{ts}] {role}: {content}…")
    return "\n".join(lines)

async def main():
    try:
        # Read hook input (not needed here, but keeps structure consistent)
        _hook_input = json.load(sys.stdin)
    except Exception:
        pass

    try:
        async with httpx.AsyncClient(timeout=3) as client:
            # Optional health probe; ignore failures
            try:
                await client.get(f"{BASE}/memory/health")
            except Exception:
                pass

            recent = await fetch_recent_important(client)

        primer = f"""
🧠 MEMORY PALACE PRIMER
=======================
When you query memory, prefer:
- "Recall recent, high‑salience memories about X"
- "Find related memories to <id> within 2 hops"
- "Summarize conversation history for <conversation_id>"

RECENT IMPORTANT:
{fmt_memories(recent)}

Tip: You can also use /api/v1/memory/query for advanced Cypher when needed.
"""
        # For SessionStart, stdout is injected into context on exit 0
        print(primer.strip())
        sys.exit(0)

    except Exception as e:
        print(f"⚠️ Memory Palace connection failed: {e}\nRunning without memory context.", file=sys.stderr)
        sys.exit(0)  # don't block session start

if __name__ == "__main__":
    asyncio.run(main())
```

Settings registration:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/memory_palace_startup.py",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

> Note: For SessionStart you can just print the primer (no JSON wrapper needed). If you *do* want structured control later, you can emit `{"suppressOutput":true, "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}`, but it’s optional for this one.

---

# Quick scripts for the other must‑haves

### A) Commit quality gate (PreToolUse, matcher=Bash; deny on failures)

```python
#!/usr/bin/env python3
# ~/.claude/hooks/pretooluse_git_gate.py
import json, subprocess, sys

def run(cmd):
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()

hook = json.load(sys.stdin)
tool = hook.get("tool_name")
cmd  = (hook.get("tool_input") or {}).get("command","")

if tool == "Bash" and "git commit" in cmd:
    failures = []

    # Fast hygiene first
    rc, out, err = run("ruff check --quiet")
    if rc != 0: failures.append(f"Ruff:\n{out or err}")

    rc, out, err = run("pyright")  # or mypy
    if rc != 0: failures.append(f"Type check:\n{out or err}")

    # Keep tests quick (mark slow tests and skip them here if needed)
    rc, out, err = run("pytest -q")
    if rc != 0: failures.append(f"Tests:\n{out or err}")

    if failures:
        # Deny the commit; explain to the user (Claude sees stderr)
        print(json.dumps({
          "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Pre‑commit checks failed. Fix and retry."
          }
        }))
        print("\n\n".join(failures), file=sys.stderr)
        sys.exit(2)  # block
    else:
        print(json.dumps({
          "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "Clean to commit ✅"
          }
        }))
        sys.exit(0)

# Not a git commit; do nothing
sys.exit(0)
```

Settings:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/pretooluse_git_gate.py",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

### B) Pet peeves / “coding contract” injection (UserPromptSubmit)

```python
#!/usr/bin/env python3
# ~/.claude/hooks/prompt_policy.py
import json, sys, os

PEEVES = """
Engineering Contract (short):
- Prefer pure functions; no I/O in domain layers
- Always type annotate public callables
- No bare `except:`; use specific exceptions + log context
- No print() in libs; use structured logging
- Keep functions under ~50 lines; extract helpers
"""

hook = json.load(sys.stdin)
prompt = hook.get("prompt","") or ""

extra = PEEVES.strip()

# Optional soft guard: ask for confirmation on risky intents
ASK_PATTERNS = ("rewrite the entire", "delete all", "force push")
decision = None
reason = None
if any(p in prompt.lower() for p in ASK_PATTERNS):
    decision = "block"  # or leave undefined and use permissionConfirm in PreToolUse
    reason = "Large‑scope request detected. Please clarify scope or confirm."

out = {
  "decision": decision,
  "reason": reason,
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": extra
  }
}
print(json.dumps(out))
sys.exit(0 if decision is None else 2)
```

Settings:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/prompt_policy.py" } ] }
    ]
  }
}
```

### C) Commit nudge (Stop)

```python
#!/usr/bin/env python3
# ~/.claude/hooks/stop_commit_nudge.py
import json, subprocess, sys, time

def run(cmd): return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()

_ = json.load(sys.stdin)  # not used
last = run("git log -1 --format=%ct") or "0"
age_min = (time.time() - int(last)) / 60
changed = run("git status --porcelain")
files = [l for l in changed.splitlines() if l]

if age_min > 20 or len(files) > 20:
    print("💡 It’s been a while or many files changed — consider `git commit -m '…'`.", flush=True)

sys.exit(0)
```

Settings:

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command", "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/stop_commit_nudge.py" } ] }
    ]
  }
}
```

---

# About your JSON DSL question

**Implement the SessionStart hook now, with today’s endpoints.** It already delivers big value and has no external dependency.

Then:

1. **Add a tiny `/api/v1/memory/stats`** endpoint (friend/claude names, total count, avg salience) and (optionally) `/api/v1/topics/active` (top N topic\_ids + counts). Both are quick.

2. **Introduce the JSON DSL + unified `/api/v1/query`** next. Your hook can then switch from the simple `/memory/recall` call to the richer DSL query when it exists. Until then, your primer is still great.

---

If you want, I can wire the two tiny endpoints (`/stats`, `/topics/active`) in your FastAPI app and open a PR. Or we keep things minimal and ship the simple SessionStart today.
