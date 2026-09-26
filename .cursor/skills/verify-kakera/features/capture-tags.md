# Add capture tags

Capture tags let a user attach labels to a capture from the command line. An invalid tag aborts before any capture. A valid tag is accepted and then the capture still follows the URL rules, including a missing-cookie refusal that writes no note.

## Sub-features

- `tag-apply` adds one or more `--tag` values to a capture.
- `tag-reject-invalid` aborts on a tag that cannot be an Obsidian tag.
- `tag-then-refuse` accepts a valid tag and still refuses a URL that cannot be saved, without writing a note.

## How to get to it (user POV)

- Run `./kakera --tag TAG URL`.
- Repeat the flag: `./kakera --tag research --tag reference URL`.
- Combine with compose: `./kakera --compose --tag TAG URL1 URL2`.
- Run `./kakera inbox --tag TAG` or `./kakera todoist --tag TAG` to tag each group that command processes. Those queue commands are specified in the inbox feature and are not a substitute for this command's URL form.

## Driving it with tmux

Preconditions:

- Doctor printed `ok: kakera verify doctor`.
- `$SESSION` is the tmux session from Launch.
- The safe entry points are `tag-reject-invalid` and `tag-then-refuse`. Do not use a live URL to prove a tag written into a note.

- **Invalid tag.** Submit a numeric tag with a post-shaped URL. Run inside `$SESSION`:

  ```sh
  ./kakera --tag 2024 "https://www.instagram.com/p/KakeraVerify1/" \
    > "$VERIFY_ROOT/tag-invalid-stdout.txt" 2> "$VERIFY_ROOT/tag-invalid-stderr.txt"
  printf '%s\n' "$?" > "$VERIFY_ROOT/tag-invalid-exit.txt"
  echo KAKERA_VERIFY_DONE
  ```

  Exit code is `2`. Stdout is empty. Stderr includes the usage header and ends with `kakera.py: error: invalid Obsidian tag: '2024'`. The vault has no files.
- **Valid tag, then missing cookies.** Submit a legal tag and the absent Instagram account. Run inside `$SESSION`:

  ```sh
  ./kakera --tag reading --account verify-absent "https://www.instagram.com/p/KakeraVerify1/" \
    > "$VERIFY_ROOT/tag-refuse-stdout.txt" 2> "$VERIFY_ROOT/tag-refuse-stderr.txt"
  printf '%s\n' "$?" > "$VERIFY_ROOT/tag-refuse-exit.txt"
  echo KAKERA_VERIFY_DONE
  ```

  Exit code is `1`. Stderr is empty. Stdout's suffix is `error: https://www.instagram.com/p/KakeraVerify1/: Instagram account 'verify-absent' has no saved cookies`. The vault still has no files, so the tag was not stored.
- **Proof.** Record entry `tag-reject-invalid` or `tag-then-refuse`, the command, stdout, stderr, exit code, and the vault file count. A note whose tags include `reading` is the success state of `tag-apply` and is not either refusal.

## Gotchas

- Invalid `--tag` values exit `2` before any URL is fetched. A later URL error is not reached.
- `2024` is invalid. `year/2024` is valid. The verification command uses `2024` exactly.
- Whitespace in a tag becomes `-` only when a capture is actually saved. This refusal path does not show that normalization.
- `--tag` on `instagram-cookies`, `twitter-cookies`, or `reddit-oauth` is a usage error. Do not run those commands to prove it; they can read a browser session or rewrite config.
- `--tag share/telegram-only` is rejected on a direct capture. It is only a queue request on inbox or Todoist, which this drive does not start.
- Tags are compared case-insensitively when a note is written. The refusal proof has no note to compare.
