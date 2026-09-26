# Process the inbox

Inbox lets a user keep unchecked Markdown tasks in a note inside the configured vault. Kakera reads that note, captures each pending group, and checks off a group only after it succeeds. A missing inbox note is created. An empty inbox reports that nothing is waiting. An unsupported URL stays unchecked.

## Sub-features

- `inbox-create` creates the inbox note when the configured file is absent.
- `inbox-empty` reports that a seeded inbox has no pending links and does not change it.
- `inbox-reject-unsupported` leaves an unchecked unsupported URL unchecked.
- `inbox-watch` polls the inbox until the user stops it. It is not a one-shot verification drive.

## How to get to it (user POV)

- Run `./kakera inbox` to process the configured inbox once.
- Run `./kakera inbox --watch` to poll until Ctrl-C.
- Run `./kakera inbox --watch --interval 30s` to override the poll interval for that watch.
- Run `./kakera inbox --tag TAG` to add a capture tag to each group processed by that invocation.
- Put tasks in the inbox note as unchecked Markdown items, for example `- [ ] https://www.instagram.com/p/ABC/`.

## Driving it with tmux

Preconditions:

- Doctor printed `ok: kakera verify doctor`.
- `$SESSION` is the tmux session from Launch.
- The isolated config sets `obsidian.inbox` to `kakera/inbox.md` under `$VERIFY_ROOT/vault`.
- Start from a vault whose `kakera/inbox.md` does not exist yet when proving `inbox-create`.
- Do not pass URL arguments, `--watch`, `--compose`, or a live source URL.

- **Create a missing inbox.** Run `./kakera inbox` before the file exists. Run inside `$SESSION`:

  ```sh
  test ! -e "$VERIFY_ROOT/vault/kakera/inbox.md"
  ./kakera inbox > "$VERIFY_ROOT/inbox-create-stdout.txt" 2> "$VERIFY_ROOT/inbox-create-stderr.txt"
  printf '%s\n' "$?" > "$VERIFY_ROOT/inbox-create-exit.txt"
  echo KAKERA_VERIFY_DONE
  ```

  Exit code is `0`. Stderr is empty. Stdout's suffix is `ok: created $VERIFY_ROOT/vault/kakera/inbox.md`. The file's entire contents are `# Kakera Inbox` followed by a blank line.
- **Empty inbox.** After that file exists, run `./kakera inbox` again. Exit code is `0`. Stdout is exactly `ok: no pending links` with no timestamp. Stderr is empty. The file bytes are unchanged.
- **Unsupported task.** Replace the file with exactly these two lines plus a trailing newline: `# Kakera Inbox` and `- [ ] https://example.com/not-a-post`. Run inside `$SESSION`:

  ```sh
  printf '%s\n' '# Kakera Inbox' '- [ ] https://example.com/not-a-post' \
    > "$VERIFY_ROOT/vault/kakera/inbox.md"
  strace -f -e trace=connect,execve -o "$VERIFY_ROOT/inbox-reject.strace" \
    ./kakera inbox \
    > "$VERIFY_ROOT/inbox-reject-stdout.txt" 2> "$VERIFY_ROOT/inbox-reject-stderr.txt"
  printf '%s\n' "$?" > "$VERIFY_ROOT/inbox-reject-exit.txt"
  echo KAKERA_VERIFY_DONE
  ```

  Exit code is `1`. Stdout's suffix is `error: https://example.com/not-a-post: supported sources are Instagram, Twitter/X, Reddit, and RedNote`. The task still contains `[ ]`, not `[x]`. `inbox-reject.strace` has no `connect(` line and no `gallery_dl` or `gallery-dl` exec.
- **Proof.** Record entry `inbox-reject-unsupported` or the entry actually run, the command, stdout, stderr, exit code, and the inbox file bytes after the command. A checked task is the success state of a real capture and is not this entry point.

## Gotchas

- The first `./kakera inbox` creates the note and returns. It does not also print `ok: no pending links`. Seed the file before proving the empty state.
- `ok: no pending links` has no timestamp. Create and error lines do.
- `./kakera inbox` does not accept URL arguments. Put the URL in the note.
- `--watch` runs until Ctrl-C. A one-shot proof must not start it. If it starts, kill `$SESSION` in cleanup; do not kill every Python process.
- `--interval` without `--watch` is a usage error. Telegram's `./kakera telegram --watch` rejects `--interval`; that command is not inbox.
- An unsuccessful group stays unchecked. Do not mark the task by hand and call that a Kakera result.
- Queue reports need Telegram. This config has no `telegram` object and no bot token. The unsupported-task drive still prints the error line and does not send a report.
