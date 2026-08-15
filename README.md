# Kakera

Kakera saves images and source metadata from Instagram, Twitter/X, Reddit, and
public RedNote posts as portable Markdown Source Notes. It can write ordinary
local folders or the configured folders inside an Obsidian vault.

## Requirements

- macOS or Linux
- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- A supported browser session when a source requires login

`gallery-dl` is declared by `kakera.py` and installed automatically by `uv`.
Safari cookie access and the Safari export example below apply to macOS; use a
browser supported by gallery-dl on Linux.

## Setup

From a checkout of this repository:

```sh
cd /path/to/kakera
cp kakera.example.json kakera.json
```

Edit `kakera.json` with the browser name and, when using Obsidian, the vault
paths. Common browser names include `safari`, `chrome`, `firefox`, `brave`,
`edge`, and `orion`. The launcher runs Kakera through `uv`, so no separate
gallery-dl installation is needed.

Authorize Reddit once if you need Reddit captures:

```sh
./kakera reddit-oauth YOUR_REDDIT_USERNAME
```

Gallery-dl stores the refresh token in its local cache. Kakera writes the
non-secret Reddit client ID and user-agent to `kakera.json`; never publish the
refresh token printed by gallery-dl.

For a convenient global command on macOS or Linux:

```sh
mkdir -p ~/.local/bin
ln -s "$PWD/kakera" ~/.local/bin/kakera
```

Add `~/.local/bin` to `PATH` if necessary.

## Commands

Only the URL capture modes accept multiple URL arguments. The default URL mode
writes to the configured Obsidian folders; `local` writes to this repository's
local folders. Inbox, Todoist, cookie-export, and OAuth commands do not accept
URLs.

| Command | Purpose |
| --- | --- |
| `./kakera URL [URL ...]` | Capture URL(s) into the configured Obsidian folders. |
| `./kakera local URL [URL ...]` | Capture URL(s) into `downloads/` and `attachments/`. |
| `./kakera --compose URL [URL ...]` | Compose URL(s) into one Source Note; the first URL stays primary. |
| `./kakera local --compose URL [URL ...]` | Compose into local `downloads/` and `attachments/`. |
| `./kakera [local] [--compose] [--tag TAG]... URL [URL ...]` | Add repeatable Capture Tags to direct, local, or composed captures; Inbox and Todoist accept the same `[--tag TAG]...` modifier without URL arguments. |
| `./kakera --browser safari URL` | Use Safari cookies for this run; overrides `browser` in `kakera.json`. |
| `./kakera --account personal URL` | Use a saved Instagram cookie account. |
| `./kakera --twitter-account imjma URL` | Use a saved Twitter/X cookie account. |
| `./kakera inbox` | Process unchecked URL tasks in the configured Obsidian inbox. |
| `./kakera inbox --watch` | Process the inbox and poll it every 2 seconds until Ctrl-C. |
| `./kakera todoist` | Process open tasks in the configured Todoist project. |
| `./kakera todoist --watch` | Process Todoist and poll every 30 seconds until Ctrl-C. |
| `./kakera instagram-cookies ALIAS --browser safari` | Save the browser's Instagram cookies locally. |
| `./kakera twitter-cookies ALIAS --browser safari` | Save the browser's Twitter/X cookies locally. |
| `./kakera reddit-oauth [USERNAME]` | Authorize Reddit and update `kakera.json`; without a username it prompts. |
| `./kakera --help` | Show all parser options. |
| `./kakera --version` | Show the Kakera version. |

Options may follow the subcommand. These are valid examples:

```sh
./kakera --browser safari URL
./kakera local --browser safari URL
./kakera inbox --browser safari --watch
./kakera todoist --browser safari --watch
./kakera --account personal URL
./kakera local --twitter-account imjma URL
./kakera --compose URL1 URL2
./kakera local --compose URL1 URL2
./kakera --tag research --tag reference URL
./kakera --compose --tag research URL1 URL2
./kakera local --compose --tag research URL1 URL2
./kakera inbox --tag reading
./kakera todoist --tag reading --watch
```

`--browser safari` in these examples overrides the configured browser for that
invocation, including Inbox, Todoist, and cookie-export commands. Use
`--account` or `--twitter-account` to select a saved service account; an
explicit service account takes precedence over `--browser`.

For each service, credential precedence is:

```text
explicit matching service account
  > explicit --browser
  > configured matching service account
  > configured browser
  > guest access
```

### Capture Tags

`--tag` may be repeated. It applies to every direct or local Capture in the
invocation, once to a `--compose` Capture, and to every Inbox or Todoist group
processed by that invocation, including later groups in `--watch`. Ordinary
multiple URL arguments remain independent Captures, so the supplied tags are
copied to each one; use `--compose` when the tags should belong to one note.

Tags are trimmed, Unicode-normalized, have one optional leading `#` removed,
and internal whitespace becomes `-`. Obsidian tag characters, Unicode
symbols/emoji, and `/` nesting are preserved. Numeric-only tags such as
`2024` and malformed slash nesting such as `/year`, `year/`, or `year//2024`
are invalid; `year/2024` is valid. Tags merge in this order:

```text
automatic service tags → existing non-service/manual tags → queue tags → CLI --tag values
```

Duplicates are compared case-insensitively; the first spelling and position
are kept. Existing non-service/manual tags survive recapture. On recomposition,
automatic service tags are recalculated from the current source list, so a
removed service tag does not remain stale. Invalid command-line tags abort
before any capture; invalid Inbox hashtags or Todoist labels are skipped with a
warning so queue metadata cannot block an otherwise valid capture. Cookie-export
commands and `reddit-oauth` reject `--tag`.

In an Inbox, inline hashtags on the parent and nested task lines become Capture
Tags in task order; URL `#fragments` are ignored. In Todoist, native labels on
the parent and nested subtasks are used in the same depth-first order as their
URLs; `#words` in Todoist text are not parsed.

`--account` is the Instagram account; `--twitter-account` is the Twitter/X
account. A saved account alias is 1–50 characters using only ASCII letters,
numbers, `_`, or `-`. Cookie export resolves its browser separately as
`--browser`, then `kakera.json`'s `browser`, then `orion`.

## Configuration

Start with this base configuration:

```json
{
  "browser": "safari",
  "obsidian": {
    "vault": "~/Documents/My Vault",
    "notes": "kakera",
    "attachments": "attachments",
    "inbox": "kakera/inbox.md"
  },
  "todoist": {
    "project_id": "YOUR_TODOIST_PROJECT_ID"
  }
}
```

Optional account fragments:

```json
{
  "instagram": {"account": "personal"},
  "twitter": {"account": "imjma"}
}
```

`obsidian.vault` is the existing vault directory; `~` is expanded. `notes`,
`attachments`, and `inbox` are paths relative to that vault: they are the
Source Note folder, Attachment folder, and Markdown inbox file respectively.
They must not be absolute paths or contain `..`, and all must remain inside
the vault.

The `reddit` section is added by `reddit-oauth`. `kakera.json`, `.cookies/`,
`downloads/`, and `attachments/` are ignored by Git. Capture coordination locks
live in the per-user runtime directory and do not appear in output folders.
Their normalized case-insensitive identity can conservatively serialize two
distinct case-sensitive paths; this trades some parallelism for safe aliasing.
Lock acquisition also has a bounded timeout, preferring a visible safety
failure over waiting forever on a damaged or abandoned lock.

### Saved browser cookies

To save a logged-in Safari Twitter/X session under the requested alias:

```sh
./kakera twitter-cookies personal --browser safari
```

Kakera reports the browser used and writes
`.cookies/twitter-personal.txt`. Instagram uses the corresponding
`.cookies/instagram-personal.txt` command:

```sh
./kakera instagram-cookies personal --browser safari
```

The export keeps only cookies for the service domains, requires the
authentication cookies, creates `.cookies/` with mode `700`, and writes each
cookie file with mode `600`. Cookie files are Netscape-format files that can be
copied to another machine running Kakera, but they are bearer credentials:
keep them private and do not commit them. If a cookie file is exposed, delete
it, invalidate or revoke that service session in the browser or service
account, sign in again, and re-export it. Cookies expire or are invalidated
when the browser session expires; run the save command again after signing in.
Saved account aliases are selected with
`--account` for Instagram or `--twitter-account` for Twitter/X, or by adding
the optional account fragment above.

## Todoist inbox on iOS (optional)

Create a Todoist project for Kakera and put its ID in `todoist.project_id` in
`kakera.json`. To persist the personal API token for zsh, add this line to
`~/.zshrc`:

```sh
export TODOIST_API_TOKEN="YOUR_TODOIST_API_TOKEN"
```

Then reload the shell:

```sh
source ~/.zshrc
```

This stores the token as plaintext in your shell configuration. Keep
`~/.zshrc` private and never commit or share it.

Follow the [Todoist iOS Shortcut setup](docs/todoist.md) to send shared URLs
directly to Todoist's API without opening the Todoist app. `kakera todoist`
reads open tasks in the configured project, collects URLs from task content and
description, then follows nested subtasks depth-first. The first URL is the
Primary Source; multiple URLs become one composition. It closes the root task
only after at least one image is saved.
Todoist captures are written to the configured `obsidian.notes` and
`obsidian.attachments` folders. Tasks without a URL remain open; a composition
with at least one saved image closes its root task even if another source
failed. If no image is saved, the task remains open. `--watch` polls every 30
seconds. Native Todoist labels on the parent and nested subtasks become
Capture Tags in the same depth-first order as their URLs. Add `--tag` for
invocation-wide tags, including watch mode; hashtags typed in Todoist text are
not interpreted as labels.

## Obsidian inbox workflow

The configured inbox is an ordinary Markdown note. Add unchecked URL tasks:

```md
# Kakera Inbox

- [ ] https://www.instagram.com/p/...
- [ ] https://x.com/USER/status/...
- [ ] https://redd.it/...
- [ ] http://xhslink.com/o/...
```

`./kakera inbox` creates the configured inbox with `# Kakera Inbox` if it does
not exist. Inbox captures are written to the configured `obsidian.notes` and
`obsidian.attachments` folders. Successful captures become `[x]`; failures
remain unchecked and are retryable. Duplicate pending URLs are processed once
per run. To compose Inbox sources, put multiple URLs on one task or use nested
tasks beneath one parent:

```md
- [ ] Compose #research
  - [ ] https://www.instagram.com/p/...#photo #first
  - [ ] https://x.com/USER/status/... #second
```

URLs and inline hashtags are collected from the parent first, then from nested
tasks depth-first; the URL `#photo` fragment above is ignored as a tag. The
resulting tag order is `research`, `first`, `second`. Successful groups check
the parent and its subtasks. Failed sources are recorded in the Source Note; if
no image is saved, the group remains unchecked. `--watch`
rechecks the file every 2 seconds and stops with Ctrl-C. Add `--tag` to apply
an invocation-wide tag to every group, including later groups processed by
watch mode.

If an iCloud-backed inbox reports `Operation not permitted` even after granting
your terminal Full Disk Access, the Obsidian app-specific iCloud container may
be blocking command-line access. Use a local vault or a normal iCloud Drive
path visible in Finder; changing Python or uv permissions will not fix that
File Provider restriction.

## Supported inputs

Ordinary multiple URL arguments are independent captures. Use `--compose` when
the URLs should become one Source Note. The first submitted URL supplies the
frontmatter title, primary URL, and filename identity; author properties are
included when available. Each additional
source is appended as a Markdown section with its own metadata, text, and
images. Sources are deduplicated by canonical URL. A composition is saved when
at least one source supplies an image, and failures are recorded in the note.

Attachments keep their original service/post identity and are never deleted
when a later recomposition removes a source section. Recomposition recalculates
automatic service tags from the current source list while preserving existing
non-service/manual tags and adding current queue or CLI tags.

| Service | Accepted URL examples | Capture behavior |
| --- | --- | --- |
| Instagram | `https://www.instagram.com/p/ID/`, `/reel/ID/`, `/tv/ID/` | Uses gallery-dl; login cookies may be required. |
| Twitter/X | `https://x.com/USER/status/ID`, `https://x.com/i/web/status/ID`, `twitter.com/.../status/ID`, optional `/photo/N` or `/video/N` | Individual status only; images are saved and videos are ignored. |
| Reddit | `https://redd.it/ID`, Reddit `/comments/ID/` or `/gallery/ID/` URLs | Uses gallery-dl; configure Reddit OAuth when required. |
| RedNote | `http(s)://xhslink.com/o/ID`, public `xiaohongshu.com/explore/ID` or `/item/ID` | Reads public page data and images; no login-only posts. |

## Output

Local captures produce one Source Note and service-scoped Attachments:

```text
# ./kakera local URL
downloads/
  Post title - Instagram.md
attachments/
  instagram/
    instagram-POST_ID-01.jpg
```

The default `./kakera URL` uses the configured Obsidian paths instead:

```text
vault/
  kakera/
    Post title - Instagram.md
    inbox.md
  attachments/
    instagram/
      instagram-POST_ID-01.jpg
```

Notes contain Obsidian properties, source text when available, and relative
image links. Repeated captures of the same post deduplicate same-content
Attachments. If another post uses the same title, the later note includes its
post ID and service in the filename, for example
`Post title - POST_ID - Instagram.md`.

## Limitations

- Instagram, Reddit, and Twitter/X depend on gallery-dl extractors and may
  change, rate-limit, or require a current logged-in browser session.
- Twitter/X accepts individual status URLs and saves images only. Text-only,
  video-only, and image-free posts return `no supported images found`; videos
  in mixed posts are ignored.
- RedNote is limited to public page data and has a 5 MB page and 50 MB per-image
  limit. Gallery-dl captures are limited to 50 MB per file.
- Kakera does not bypass login walls, CAPTCHAs, private-post restrictions, or
  access controls, and native iOS/macOS apps are not part of v0.1.0.
- Kakera processes only URLs you provide and does not send URLs, cookies, or
  media to a Kakera service.

Only save content you have permission to retain.

## Development

```sh
python3 test_kakera.py
./kakera --version
git diff --check
```

The tests use Python's standard library and mocks; they do not contact social
sites or read real browser cookies.

## License

[MIT](LICENSE)
