# Compose submitted URLs

Compose lets a user put several submitted URLs into one Source Note. The first URL stays the primary source. When none of the URLs produce a supported image, the command fails and writes no note.

## Sub-features

- `compose-obsidian` composes two or more URLs into one note in the configured Obsidian folders.
- `compose-local` composes into the checkout `downloads/` and `attachments/` folders.
- `compose-single` treats one unique URL like a single capture.
- `compose-reject-unsupported` fails when every URL is unsupported and writes nothing.

## How to get to it (user POV)

- Run `./kakera --compose URL1 URL2` to compose into the configured Obsidian folders.
- Run `./kakera local --compose URL1 URL2` to compose into the checkout folders.
- Run `./kakera --compose URL` when there is only one URL.
- Add tags with `./kakera --compose --tag TAG URL1 URL2`. The tag belongs to the one composed note.

## Driving it with tmux

Preconditions:

- Doctor printed `ok: kakera verify doctor`.
- `$SESSION` is the tmux session from Launch.
- The safe entry point is `compose-reject-unsupported`. Do not compose live source URLs and do not use `local`.

- **Unsupported composition.** Submit two unsupported HTTPS URLs. Run inside `$SESSION`:

  ```sh
  strace -f -e trace=connect,execve -o "$VERIFY_ROOT/compose.strace" \
    ./kakera --compose "https://example.com/a" "https://example.com/b" \
    > "$VERIFY_ROOT/compose-stdout.txt" 2> "$VERIFY_ROOT/compose-stderr.txt"
  printf '%s\n' "$?" > "$VERIFY_ROOT/compose-exit.txt"
  echo KAKERA_VERIFY_DONE
  ```

  Exit code is `1`. Stderr is empty. Stdout's suffix is `error: https://example.com/a, https://example.com/b: no sources produced supported images`.
- **Confirm nothing was stored.** The vault has no files. `compose.strace` has no `connect(` line and no `gallery_dl` or `gallery-dl` exec. `git status --short` is empty.
- **Proof.** Record entry `compose-reject-unsupported`, the command, stdout, stderr, exit code, and the vault file count. A composed note on disk is the success state of `compose-obsidian` and is not this entry point.

## Gotchas

- One unique URL does not create a multi-source note. The result line matches a single capture of that URL.
- Duplicate URLs that canonicalize to the same post are one source. The submitted tracking query is not a second source.
- `./kakera local --compose` writes into the checkout. Verification uses the default Obsidian target and the isolated vault.
- Inbox and Todoist compose task groups by themselves. `--compose` on `./kakera inbox` or `./kakera todoist` is a usage error. Those commands are not this feature's entry points.
- A partial success (one good source and one bad source) still writes a note. Do not use a live URL to prove the all-failed path.
