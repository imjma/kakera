# Capture a submitted URL

Capture lets a user turn one Instagram, Twitter/X, Reddit, or RedNote URL into a Source Note and attachments, either in the configured Obsidian folders or in the checkout's local folders. An unsupported URL, a non-HTTPS URL, or a named account with no saved cookies fails in the terminal and leaves those folders untouched.

## Sub-features

- `capture-obsidian` saves a supported URL into the configured notes and attachments folders.
- `capture-local` saves a supported URL into the checkout `downloads/` and `attachments/` folders.
- `capture-reject-http` rejects a non-HTTPS URL.
- `capture-reject-unsupported` rejects an HTTPS URL that is not a supported source.
- `capture-reject-missing-instagram` rejects a post URL when the named Instagram account has no saved cookies.
- `capture-reject-missing-twitter` rejects a status URL when the named Twitter/X account has no saved cookies.

## How to get to it (user POV)

- Run `./kakera URL` from a checkout. The launcher selects the configured Obsidian folders.
- Run `./kakera local URL` to use the checkout's `downloads/` and `attachments/` folders.
- Run `./kakera --account ALIAS URL` to select a saved Instagram cookie account.
- Run `./kakera --twitter-account ALIAS URL` to select a saved Twitter/X cookie account.
- Run `./kakera --browser BROWSER URL` to override the configured browser for that run.

## Driving it with tmux

Preconditions:

- Doctor printed `ok: kakera verify doctor`.
- `$SESSION` is the tmux session from Launch.
- `$KAKERA_CONFIG` is the isolated file whose `browser` is `none`.
- `.cookies/instagram-verify-absent.txt` and `.cookies/twitter-verify-absent.txt` are absent.
- The verification entry point is `capture-reject-missing-instagram`. Do not substitute a live post, `capture-local`, or `--browser safari`.

- **Missing Instagram cookies.** Submit one post URL for an account that has no saved cookies. Run inside `$SESSION`:

  ```sh
  sha256sum "$KAKERA_CONFIG" > "$PROOF_DIR/capture-url-config.sha256"
  printf '%s\n' './kakera --account verify-absent "https://www.instagram.com/p/KakeraVerify1/"' > "$PROOF_DIR/capture-url-command.txt"
  strace -f -e trace=connect,execve -o "$PROOF_DIR/capture-url-strace.txt" \
    ./kakera --account verify-absent "https://www.instagram.com/p/KakeraVerify1/" \
    > "$PROOF_DIR/capture-url-stdout.txt" 2> "$PROOF_DIR/capture-url-stderr.txt"
  printf '%s\n' "$?" > "$PROOF_DIR/capture-url-exit-code.txt"
  echo KAKERA_VERIFY_DONE
  ```

  Exit code is `1`. Stderr is empty. Stdout is one line whose suffix is `error: https://www.instagram.com/p/KakeraVerify1/: Instagram account 'verify-absent' has no saved cookies`. The prefix is a local timestamp `YYYY-MM-DD HH:MM:SS`.
- **Confirm nothing was stored.** List the vault and the checkout. The vault has no files. `downloads/`, `attachments/`, and `.cookies/` do not exist. `sha256sum` of `$KAKERA_CONFIG` matches `capture-url-config.sha256`. `$KAKERA_TELEGRAM_STATE` does not exist. `git status --short` is empty and `git rev-parse HEAD` is unchanged. `capture-url-strace.txt` contains no `connect(` line and no `gallery_dl` or `gallery-dl` exec.
- **HTTP rejection.** Submit `http://example.com/post` with `./kakera "http://example.com/post"`. Exit code is `1`. Stdout's suffix is `error: http://example.com/post: only HTTPS URLs are supported`. No note file appears.
- **Unsupported host.** Submit `https://example.com/not-a-post` with `./kakera "https://example.com/not-a-post"`. Exit code is `1`. Stdout's suffix is `error: https://example.com/not-a-post: supported sources are Instagram, Twitter/X, Reddit, and RedNote`. No note file appears and strace shows no `connect(`.
- **Missing Twitter cookies.** Submit `./kakera --twitter-account verify-absent "https://x.com/kakera/status/1"`. Exit code is `1`. Stdout's suffix is `error: https://x.com/kakera/status/1: Twitter account 'verify-absent' has no saved cookies`.
- **Proof.** Save the pane and the skip record for `capture-reject-missing-instagram`. Run:

  ```sh
  tmux capture-pane -p -t "$SESSION:0.0" > "$PROOF_DIR/capture-url-transcript.txt"
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

  `connect_syscalls` is `0`, `gallery_dl_execs` is `0`, `vault_files` is `0`, the three checkout dirs are `absent`, and `git_status` is `0`. The transcript contains the `./kakera --account verify-absent` command and `KAKERA_VERIFY_DONE`.

A saved Capture (a new `.md` note plus image files) is the success state of `capture-obsidian` and `capture-local`. Reaching it calls gallery-dl and the source network. This map does not verify that state.

## Gotchas

- `./kakera` without a leading mode word always passes `--obsidian`. `./kakera local` does not, and it writes inside the checkout, which this run must not share with real captures.
- There is no dry-run flag. `test_kakera.py` does not prove this command.
- The stdout timestamp changes every run. Assert the message suffix and exit code.
- Exit `1` is a capture refusal. Exit `2` is a usage error, including `account must be 1-50 letters, numbers, _ or -`.
- A present `.cookies/instagram-verify-absent.txt` would be a real session. Doctor must fail instead of driving it.
- `browser` `none` must never be handed to gallery-dl. If strace shows a `gallery_dl` exec, the refusal proof is invalid.
- Several URL arguments are separate captures. One refusal does not describe the others.
