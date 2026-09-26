---
name: verify-kakera
description: "Drive Kakera's CLI — the primary surface of this checkout — and prove capture, compose, tag, and inbox behavior with an isolated kakera.json. Use when verifying Kakera user-facing commands, proving a capture refusal, or checking the CLI without live Instagram, Twitter/X, Reddit, RedNote, Telegram, Todoist, browser cookies, or the Synology stack."
---

# Verify Kakera

Kakera is a short-lived Python CLI. A user runs `./kakera` from a checkout. There is no server to keep up and no dry-run or test mode. Verification installs dependencies once, then starts each drive in its own tmux session against an isolated config and vault.

`python test_kakera.py` is the unit-test script. It is not a user path. `make restart` only rebuilds the Synology docker stack in `deploy/synology`. Do not use it as launch.

Do not drive live Instagram, Twitter/X, Reddit, RedNote, Telegram, or Todoist accounts. Do not read or write a user's `kakera.json`, browser cookies, `.cookies/`, or the Synology deploy. Do not run `instagram-cookies`, `twitter-cookies`, `reddit-oauth`, `telegram`, `todoist`, `share`, or `--watch`.

## Launch

From the repository root (the directory that contains `./kakera`):

```sh
cd /path/to/kakera
export PATH="$HOME/.local/bin:$PATH"
command -v uv
command -v tmux
command -v strace
RUN_ID="$(date +%Y%m%d%H%M%S)"
VERIFY_ROOT="$(mktemp -d "/tmp/kakera-verify-${RUN_ID}-XXXX")"
mkdir -p "$VERIFY_ROOT/vault/kakera" "$VERIFY_ROOT/vault/attachments"
cat > "$VERIFY_ROOT/kakera.json" <<EOF
{
  "browser": "none",
  "obsidian": {
    "vault": "$VERIFY_ROOT/vault",
    "notes": "kakera",
    "attachments": "attachments",
    "inbox": "kakera/inbox.md",
    "interval": "2s"
  }
}
EOF
export KAKERA_CONFIG="$VERIFY_ROOT/kakera.json"
export KAKERA_TELEGRAM_STATE="$VERIFY_ROOT/kakera.telegram-state.json"
unset TELEGRAM_BOT_TOKEN
unset TODOIST_API_TOKEN
export PROOF_DIR="/cursor/stores/bc-62ef6805-149a-4c9a-95c1-24137bf3b93c/media/verify-kakera"
mkdir -p "$PROOF_DIR"
SESSION="kakera-verify-${RUN_ID}"
```

`KAKERA_CONFIG` is the only config Kakera reads (`CONFIG` in `kakera.py`). When it is unset, Kakera reads `./kakera.json` in the checkout. The isolated file omits `telegram` and `todoist`. `browser` is the sentinel `none`: it is not a gallery-dl browser name, and the drives in this skill return before gallery-dl reads it. `./kakera local` is not isolated; it writes `downloads/` and `attachments/` inside the checkout. Do not use `local` for a verification drive.

Ready means `./kakera --version` prints exactly `Kakera 0.1.0` on stdout and exits 0. The first `uv run` may use the network and print `Downloading` / `Installed` on stderr while it fills the uv cache. Run version once more after that. The second run's stderr is empty. That network use is dependency install, not a capture.

```sh
./kakera --version > "$PROOF_DIR/launch-version.txt" 2> "$PROOF_DIR/launch-version.err"
test "$(cat "$PROOF_DIR/launch-version.txt")" = "Kakera 0.1.0"
if [ -s "$PROOF_DIR/launch-version.err" ]; then
  ./kakera --version > "$PROOF_DIR/launch-version.txt" 2> "$PROOF_DIR/launch-version.err"
fi
test "$(cat "$PROOF_DIR/launch-version.txt")" = "Kakera 0.1.0"
test ! -s "$PROOF_DIR/launch-version.err"
```

There is no listening port. Readiness is that version line, not a log from `make`. Open the tmux session in Drive only after doctor passes.

## Doctor

Run this before every drive. It is read-only: version, a no-URL parse, and filesystem checks. It does not create an inbox, notes, attachments, or cookies. Exit 0 prints `ok: kakera verify doctor`.

```sh
doctor() {
  set -euo pipefail
  test -n "${KAKERA_CONFIG:-}"
  test -f "$KAKERA_CONFIG"
  test "$KAKERA_CONFIG" != "$PWD/kakera.json"
  test -z "${TELEGRAM_BOT_TOKEN:-}"
  test -z "${TODOIST_API_TOKEN:-}"
  test ! -e "$PWD/.cookies/instagram-verify-absent.txt"
  test ! -e "$PWD/.cookies/twitter-verify-absent.txt"
  python3 - <<'PY'
import json, os
from pathlib import Path
config = json.loads(Path(os.environ["KAKERA_CONFIG"]).read_text())
vault = Path(config["obsidian"]["vault"]).expanduser()
assert vault.is_dir(), vault
assert config.get("browser") == "none"
assert "telegram" not in config and "todoist" not in config
PY
  version="$(./kakera --version)"
  test "$version" = "Kakera 0.1.0"
  set +e
  ./kakera > "$VERIFY_ROOT/doctor-nourl.out" 2> "$VERIFY_ROOT/doctor-nourl.err"
  code=$?
  set -e
  test "$code" -eq 2
  test ! -s "$VERIFY_ROOT/doctor-nourl.out"
  grep -q "provide at least one URL" "$VERIFY_ROOT/doctor-nourl.err"
  test ! -e "$KAKERA_TELEGRAM_STATE"
  printf '%s\n' "ok: kakera verify doctor"
}
set -o pipefail
doctor | tee "$PROOF_DIR/doctor.txt"
test "$(cat "$PROOF_DIR/doctor.txt")" = "ok: kakera verify doctor"
```

The no-URL check exits 2 because the `./kakera` launcher always appends `--obsidian`, which resolves the isolated vault and then reports that no URL was given. A missing vault, `KAKERA_CONFIG` pointing at the checkout `kakera.json`, a set token, or a saved `verify-absent` cookie file means this instance is not worth driving. An existing checkout `kakera.json` is left unread; do not open it.

## Drive

The harness is one tmux session per run, named `$SESSION`. Open it only after doctor passes. Pass the isolated environment in; do not rely on the tmux shell rc to export it. Send each user command into that session. Do not drive a session this run did not create.

```sh
tmux new-session -d -s "$SESSION" -c "$PWD" \
  -e "PATH=$PATH" \
  -e "KAKERA_CONFIG=$KAKERA_CONFIG" \
  -e "KAKERA_TELEGRAM_STATE=$KAKERA_TELEGRAM_STATE" \
  -e "PROOF_DIR=$PROOF_DIR" \
  -e "VERIFY_ROOT=$VERIFY_ROOT"
```

Wait for the sentinel the command prints. Do not treat a fixed sleep as success.

```sh
wait_done() {
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    if tmux capture-pane -p -t "$SESSION:0.0" | grep -q KAKERA_VERIFY_DONE; then
      return 0
    fi
    sleep 0.25
  done
  tmux capture-pane -p -t "$SESSION:0.0" >&2
  return 1
}
```

Feature recipes live in `features/`. Read `features/README.md` first. Prove one feature end to end; the map lists the rest so a later run can cover them. The proved entry for this skill is `capture-reject-missing-instagram` in `features/capture-url.md`.

Write that entry's shell body to `$VERIFY_ROOT/drive-capture-url.sh` and start it with `bash` so the pane does not contain `KAKERA_VERIFY_DONE` until the script prints it. Pasting the sentinel into the pane makes `wait_done` return before `./kakera` runs. The scratch script is removed with `$VERIFY_ROOT`.

```sh
cat > "$VERIFY_ROOT/drive-capture-url.sh" <<'EOF'
sha256sum "$KAKERA_CONFIG" > "$PROOF_DIR/capture-url-config.sha256"
printf '%s\n' './kakera --account verify-absent "https://www.instagram.com/p/KakeraVerify1/"' > "$PROOF_DIR/capture-url-command.txt"
strace -f -e trace=connect,execve -o "$PROOF_DIR/capture-url-strace.txt" \
  ./kakera --account verify-absent "https://www.instagram.com/p/KakeraVerify1/" \
  > "$PROOF_DIR/capture-url-stdout.txt" 2> "$PROOF_DIR/capture-url-stderr.txt"
printf '%s\n' "$?" > "$PROOF_DIR/capture-url-exit-code.txt"
echo KAKERA_VERIFY_DONE
EOF
tmux send-keys -t "$SESSION:0.0" "bash '$VERIFY_ROOT/drive-capture-url.sh'" C-m
wait_done
tmux capture-pane -p -t "$SESSION:0.0" > "$PROOF_DIR/capture-url-transcript.txt"
sha256sum -c "$PROOF_DIR/capture-url-config.sha256"
{
  printf 'entry=capture-reject-missing-instagram\n'
  printf 'connect_syscalls=%s\n' "$(grep -c 'connect(' "$PROOF_DIR/capture-url-strace.txt" || true)"
  printf 'gallery_dl_execs=%s\n' "$(grep -c -E 'gallery_dl|gallery-dl' "$PROOF_DIR/capture-url-strace.txt" || true)"
  printf 'vault_files=%s\n' "$(find "$VERIFY_ROOT/vault" -type f | wc -l | tr -d ' ')"
  printf 'checkout_downloads=%s\n' "$(if [ -d downloads ]; then echo present; else echo absent; fi)"
  printf 'checkout_attachments=%s\n' "$(if [ -d attachments ]; then echo present; else echo absent; fi)"
  printf 'checkout_cookies=%s\n' "$(if [ -d .cookies ]; then echo present; else echo absent; fi)"
  printf 'git_status=%s\n' "$(git status --short | wc -l | tr -d ' ')"
  printf 'head=%s\n' "$(git rev-parse HEAD)"
} > "$PROOF_DIR/capture-url-skipped.txt"
```

Require exit code `1`, empty stderr, stdout suffix `error: https://www.instagram.com/p/KakeraVerify1/: Instagram account 'verify-absent' has no saved cookies`, `connect_syscalls=0`, `gallery_dl_execs=0`, `vault_files=0`, checkout dirs `absent`, and `git_status=0`. The transcript must contain both `bash` of `drive-capture-url.sh` and `KAKERA_VERIFY_DONE`.

Kakera has no dry-run and no test mode. A URL that reaches `gallery-dl` uses the network. These drives stop earlier. Observe that; do not infer it from the word "error".

Observed on the missing-cookie capture, an unsupported URL, compose of two unsupported URLs, an invalid tag, and an inbox task whose URL is not a supported source, all with this isolated config:

- `strace -f -e trace=connect,execve` shows no `connect(` lines and no `gallery_dl` or `gallery-dl` exec. The execs are `./kakera`, `dirname`, `realpath`, `uv`, and the uv-managed `python`.
- The vault gains no files. `downloads/`, `attachments/`, and `.cookies/` are not created in the checkout.
- `git status --short` stays empty and `HEAD` does not move.
- `kakera.json` bytes and `KAKERA_TELEGRAM_STATE` are not written.
- The first `./kakera --version` on a cold uv cache does use the network to install pinned dependencies (`gallery-dl`, `pillow`). Later version and refusal drives do not.

`--share telegram-only` without `TELEGRAM_BOT_TOKEN`, when `telegram` is present in config, exits 2 with `TELEGRAM_BOT_TOKEN is not set` and also makes no `connect(`. This isolated config has no `telegram` object, so that command instead exits 2 with `invalid kakera.json: telegram must be an object`. Do not add Telegram config to make that path runnable.

## Evidence

Proof artifacts go only in:

`/cursor/stores/bc-62ef6805-149a-4c9a-95c1-24137bf3b93c/media/verify-kakera`

That directory is `$PROOF_DIR`. Cleanup must not delete it. Do not commit it to the Kakera repo.

A proof of `capture-reject-missing-instagram` records the user action and the resulting state:

- `$PROOF_DIR/launch-version.txt` and `$PROOF_DIR/launch-version.err`
- `$PROOF_DIR/doctor.txt`
- `$PROOF_DIR/capture-url-config.sha256` — config digest taken before the drive
- `$PROOF_DIR/capture-url-command.txt` — the exact command
- `$PROOF_DIR/capture-url-transcript.txt` — the tmux pane, including the typed command and `KAKERA_VERIFY_DONE`
- `$PROOF_DIR/capture-url-stdout.txt`, `$PROOF_DIR/capture-url-stderr.txt`, `$PROOF_DIR/capture-url-exit-code.txt`
- `$PROOF_DIR/capture-url-strace.txt` — `strace -f -e trace=connect,execve`
- `$PROOF_DIR/capture-url-skipped.txt` — counts of `connect(` and gallery-dl execs, vault files, checkout output dirs, `git status`, and `HEAD`

Standards:

- Drive `./kakera` in tmux. Do not call `save`, `main`, or other functions from `test_kakera.py`.
- Capture the command and the refusal text, then a second view that no note or attachment appeared.
- The safe path is an early refusal, not a mode named dry-run. Confirm skipped network, files, and git refs from `capture-url-skipped.txt`.
- A successful image capture is a different entry point. Do not report this refusal as a saved Capture.

## Cleanup

Kill the session this run created and delete the scratch config and vault. Never kill by process name. Never delete `$PROOF_DIR`.

```sh
if [ -n "${SESSION:-}" ]; then
  tmux kill-session -t "=$SESSION" 2>/dev/null || true
fi
if [ -n "${VERIFY_ROOT:-}" ] && [ -d "$VERIFY_ROOT" ]; then
  rm -rf "$VERIFY_ROOT"
fi
unset KAKERA_CONFIG KAKERA_TELEGRAM_STATE
```

Run that after every failed attempt before starting another session, and after a successful proof. Confirm the proof files are still in `$PROOF_DIR` after cleanup. A refusal drive does not create `downloads/`, `attachments/`, or `.cookies/`; do not delete unrelated files in the checkout.

## Helpers

This skill ships no helper scripts. The commands above and in `features/` are the harness.
