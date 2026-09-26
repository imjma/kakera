# Store one image by URL

A user can pass one HTTPS image URL, or `http://127.0.0.1` for a local fixture, and Kakera writes one Source Note plus one attachment. `./kakera URL` writes the configured Obsidian folders and does not contact Telegram. `./kakera --share telegram URL` writes the same note, adds the `share/telegram` tag, and then stops with the existing missing-token error when `TELEGRAM_BOT_TOKEN` is unset. A URL that is not an image still fails before any connection.

## Sub-features

- `image-obsidian` saves one local fixture image into the configured notes and attachments folders.
- `image-obsidian-repeat` saves that URL again and does not add a second attachment file.
- `image-share-telegram` saves the same kind of image with `--share telegram` and reports that the bot token is unset.
- `image-reject-unsupported` rejects `https://example.com/not-a-post` with no connection.

## How to get to it (user POV)

- Run `./kakera URL` from a checkout. The launcher selects the configured Obsidian folders.
- Run `./kakera --share telegram URL` to save the Capture and publish the current request to Telegram.
- Run `./kakera "https://example.com/not-a-post"` to see the unchanged refusal for a non-image URL.

## Driving it with tmux

Preconditions:

- Doctor printed `ok: kakera verify doctor` against the isolated config that has no `telegram` object.
- `$SESSION` is the tmux session from Launch.
- A fixture process this run started is serving `ridge.jpg` on `127.0.0.1` only. The JPEG bytes begin with `FF D8 FF`.
- `TELEGRAM_BOT_TOKEN` and `TODOIST_API_TOKEN` are unset.
- Do not use a live Instagram, Telegram, Reddit, or RedNote host. Do not use `./kakera local`.

- **Obsidian capture.** Submit the fixture URL with `./kakera "http://127.0.0.1:PORT/ridge.jpg"`. Exit code is `0`. Stderr is empty. Stdout's suffix is `ok: http://127.0.0.1:PORT/ridge.jpg: saved 1 image(s)`. The notes folder has one `.md` file whose frontmatter contains `image:` and the tag `image`. The attachments folder has one `image-*-01.jpg` whose bytes match the fixture.
- **Repeat.** Submit the same URL again. Exit code is `0`. The attachments folder still has that one file.
- **Share to Telegram.** Point `KAKERA_CONFIG` at a second isolated file that adds `"telegram": {"chat_id": "-1001234567890"}` and the same vault. Submit `./kakera --share telegram "http://127.0.0.1:PORT/ridge.jpg?w=2"`. Exit code is `1`. Stdout's suffix is `error: http://127.0.0.1:PORT/ridge.jpg?w=2: capture saved; Telegram failed: TELEGRAM_BOT_TOKEN is not set`. The new note contains the tags `image` and `share/telegram` and one attachment. `strace` shows a `connect(` only toward `127.0.0.1`, and no `gallery-dl` exec.
- **Non-image URL.** Submit `./kakera "https://example.com/not-a-post"` with the original isolated config. Exit code is `1`. Stdout's suffix is `error: https://example.com/not-a-post: supported sources are Instagram, Twitter/X, Reddit, and RedNote`. `strace` has no `connect(` line.

## Gotchas

- `http://127.0.0.1` is accepted so this fixture can run. `http://example.com/photo.jpg` is still rejected with `only HTTPS URLs are supported`.
- The fixture must be up before `./kakera` runs. A refused connection is not a saved Capture.
- `--share telegram` with no `telegram` object fails after the save with `telegram must be an object`. That is a different result from the missing-token result above.
- A successful send to `api.telegram.org` is not part of this recipe. Setting `TELEGRAM_BOT_TOKEN` would contact Telegram.
- `https://example.com/not-a-post` must stay a no-connection refusal. Do not treat an image capture as proof of that refusal.
