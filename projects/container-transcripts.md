# Container Transcript Access via Bind Mount

## Context

Container sessions now show up in the dashboard (bridge is working), but transcripts are inside a Docker volume (`~/.claude` in the container) which is invisible to the host. Currently we use `docker exec cat` to read them — it works but is slow, fragile, and breaks when the container stops.

## Plan: Bind-mount container `~/.claude` into the workspace

Replace the Docker volume for `~/.claude` with a bind mount to `/workspace/.claude-container/`. On the host, this appears at `~/code/<repo>/.claude-container/`.

### Changes per devcontainer.json

Replace:
```jsonc
"source=agents-claude-config-${devcontainerId},target=/home/node/.claude,type=volume"
```

With:
```jsonc
"source=${localWorkspaceFolder}/.claude-container,target=/home/node/.claude,type=bind"
```

Add `.claude-container/` to `.gitignore`.

### Changes to bridge watcher (server.py, tui.py)

Update transcript path mapping. Container transcript paths look like:
```
/home/node/.claude/projects/-workspace/UUID.jsonl
```

Map `/home/node/.claude/` → `<workspace>/.claude-container/` using the bridge dir config. The host path becomes:
```
~/code/video-gen/agents/.claude-container/projects/-workspace/UUID.jsonl
```

This is a regular file on the host — no docker exec needed.

### Remove docker exec fallback

Once the bind mount is in place, remove the `docker exec cat` code from `server.py` and `tui.py`.

### Repos to update

- `video-gen/agents`
- `video-gen/helix-agents`
- `secure-devcontainer` (template)

## Trade-offs

- **Pro**: Transcripts are plain files on the host, fast reads, work even after container stops
- **Pro**: Container auth/settings also persist without a Docker volume (same behavior, simpler mechanism)
- **Con**: `.claude-container/` directory appears in the workspace (gitignored)
- **Con**: First container start after this change will lose existing Claude auth (old volume is abandoned) — need to re-auth

## Not doing: mount tracking.db directly

Considered mounting the host's `tracking.db` into the container to eliminate the bridge entirely. SQLite over macOS Docker bind mounts (virtiofs) has locking/corruption risks. The JSONL bridge is append-only and safe. Keep it.
