# Kakera

`Kakera` (かけら, 欠片) is Japanese for a fragment, piece, or shard. Here, it reflects preserving or sharing small pieces of source posts.

Kakera saves images and source metadata from Instagram, Twitter/X, Reddit, and
public RedNote posts as local Captures with portable Markdown Source Notes. It
can write ordinary local folders or the configured folders inside an Obsidian
vault, and can publish a Telegram Delivery from a current Capture, an existing
note, a submitted URL, or a Source Service URL in an allowed user's private
Telegram message.

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

## Synology NAS

For a persistent Synology Container Manager deployment running Todoist watch,
Telegram Intake watch, and official Obsidian Headless Sync, follow the
[Synology NAS runbook](docs/synology.md).

## Commands

URL capture modes accept multiple URL arguments. `--share telegram-only` also
accepts multiple URLs and publishes each independently. The `share telegram` and
`share telegram-only` note commands accept exact note selectors, including an
existing note's exact source URL; they publish the matched note only, never
fetch that source or fall back to another command. `kakera telegram` is Telegram
Intake: it reads allowed users' private messages, not note selectors. The
default URL mode writes to the configured Obsidian folders; `local` writes to
this repository's local folders. Inbox, Todoist, Telegram Intake, cookie-export,
and OAuth commands do not accept URLs.

| Command | Purpose |
| --- | --- |
| `./kakera URL [URL ...]` | Capture URL(s) into the configured Obsidian folders. |
| `./kakera local URL [URL ...]` | Capture URL(s) into `downloads/` and `attachments/`. |
| `./kakera --compose URL [URL ...]` | Compose URL(s) into one Source Note; the first URL stays primary. |
| `./kakera local --compose URL [URL ...]` | Compose into local `downloads/` and `attachments/`. |
| `./kakera [local] [--compose] [--tag TAG]... URL [URL ...]` | Add repeatable Capture Tags to direct, local, or composed captures; Inbox and Todoist accept the same `[--tag TAG]...` modifier without URL arguments. |
| `./kakera --share telegram URL [URL ...]` | Capture to configured Obsidian folders and publish each current request to Telegram. |
| `./kakera local --share telegram URL [URL ...]` | Capture locally and publish each current request to Telegram. |
| `./kakera --compose --share telegram URL1 URL2` | Compose into one configured Source Note, then publish it to Telegram. |
| `./kakera --tag share/telegram URL` | Request Telegram delivery through the canonical current-request tag. |
| `./kakera --browser safari URL` | Use Safari cookies for this run; overrides `browser` in `kakera.json`. |
| `./kakera --account personal URL` | Use a saved Instagram cookie account. |
| `./kakera --twitter-account imjma URL` | Use a saved Twitter/X cookie account. |
| `./kakera inbox` | Process unchecked URL tasks in the configured Obsidian inbox. |
| `./kakera inbox --watch` | Process the inbox and poll it every 2 seconds until Ctrl-C. |
| `./kakera todoist` | Process open tasks in the configured Todoist project. |
| `./kakera todoist --watch` | Process Todoist and poll every 3 minutes until Ctrl-C. |
| `./kakera inbox --watch --interval 30s` | Override the watch poll interval. `30s`, `3m`, and `1h` are accepted. |
| `./kakera telegram` | Drain pending private messages to the bot (Telegram Intake) and publish recognized Source Service URLs as Transient Telegram Deliveries. |
| `./kakera telegram --watch` | Watch Telegram Intake until Ctrl-C. Long-polls; `--interval` is rejected. |
| `./kakera share telegram SELECTOR [SELECTOR ...]` | Publish existing notes from the configured notes folder; acknowledged notes ask before resending. This is manual note publishing, not capture. |
| `./kakera share telegram-only SELECTOR [SELECTOR ...]` | Publish existing notes every time, without reading, prompting on, or writing Telegram receipts. |
| `./kakera --share telegram-only URL [URL ...]` | Fetch each URL to temporary storage and publish it without creating notes, attachments, tags, queues, or receipts. |
| `./kakera instagram-cookies ALIAS --browser safari` | Save the browser's Instagram cookies locally. |
| `./kakera twitter-cookies ALIAS --browser safari` | Save the browser's Twitter/X cookies locally. |
| `./kakera reddit-oauth [USERNAME]` | Authorize Reddit and update `kakera.json`; without a username it prompts. |
| `./kakera --help` | Show all parser options. |
| `./kakera --version` | Show the Kakera version. |

Options may follow the subcommand. These are valid examples:

```sh
./kakera --browser safari URL
./kakera local --browser safari URL
./kakera --share telegram URL
./kakera local --share telegram URL
./kakera --compose --share telegram URL1 URL2
./kakera inbox --browser safari --watch
./kakera todoist --browser safari --watch
./kakera inbox --watch --interval 30s
./kakera todoist --watch --interval 1h
./kakera --account personal URL
./kakera local --twitter-account imjma URL
./kakera --compose URL1 URL2
./kakera local --compose URL1 URL2
./kakera --tag research --tag reference URL
./kakera --compose --tag research URL1 URL2
./kakera local --compose --tag research URL1 URL2
./kakera inbox --tag reading
./kakera todoist --tag reading --watch
./kakera inbox --tag share/telegram --watch
./kakera todoist --tag share/telegram --watch
./kakera inbox --tag share/telegram-only --watch
./kakera todoist --tag share/telegram-only --watch
./kakera telegram
./kakera telegram --watch
./kakera telegram --browser safari --watch
./kakera share telegram "Folder/Note.md"
./kakera share telegram-only "Folder/Note.md"
./kakera --share telegram-only https://www.instagram.com/p/ABC/
./kakera --share telegram-only --browser safari https://www.instagram.com/p/ABC/
./kakera --share telegram-only --account personal https://www.instagram.com/p/ABC/
./kakera --share telegram-only --twitter-account imjma https://x.com/user/status/123
```

`--share telegram-only` must be the first command flag; source-auth options such as
`--browser`, `--account`, or `--twitter-account` follow it. It never falls back
to `share telegram` or `share telegram-only` note publishing. Those note commands use
exact selectors—including an existing note's source URL—and never fetch or
fall back to capture publishing. `kakera telegram` is Intake, not note publishing.

`--browser safari` in these examples overrides the configured browser for that
invocation, including Inbox, Todoist, Telegram Intake, and cookie-export
commands. Use `--account` or `--twitter-account` to select a saved service
account; an explicit service account takes precedence over `--browser`.

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
    "inbox": "kakera/inbox.md",
    "interval": "2s"
  },
  "todoist": {
    "project_id": "YOUR_TODOIST_PROJECT_ID",
    "interval": "3m"
  },
  "telegram": {
    "chat_id": "-1001234567890",
    "allowed_user_ids": [123456789],
    "report_user_id": 123456789
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

`obsidian.interval` is the Inbox `--watch` poll; `todoist.interval` is the
Todoist `--watch` poll. Each value is an integer plus `s`, `m`, or `h`. These
are the built-in defaults; omit a key to keep them. `--interval` overrides the
matching key for that invocation and requires `--watch`. Telegram Intake
long-polls and rejects `--interval`.

`telegram.allowed_user_ids` is required for `kakera telegram`. It is a non-empty
array of numeric Telegram user IDs whose private messages may become Transient
Telegram Deliveries. Missing, empty, or non-numeric values refuse to start.
`telegram.report_user_id` is the one allowed user who receives Inbox and Todoist
Queue Reports in private chat. It must be listed in
`allowed_user_ids`. Omit it to skip those reports.

`obsidian.vault` is the existing vault directory; `~` is expanded. `notes`,
`attachments`, and `inbox` are paths relative to that vault: they are the
Source Note folder, Attachment folder, and Markdown inbox file respectively.
They must not be absolute paths or contain `..`, and all must remain inside
the vault.

### Telegram setup

Kakera uses the Telegram Bot API directly. Follow Telegram's [bot-creation
guide](https://core.telegram.org/bots#how-do-i-create-a-bot) with
[BotFather](https://t.me/BotFather) using `/newbot`, then treat the token like
a password. Do not put it in `kakera.json`, Git, screenshots, command
arguments, logs, or issue reports.

Add the bot to the destination channel as an administrator with **Post
Messages**, or add it to the group with permission to send messages and
photos. Kakera accepts one integer or numeric-string chat target in
`telegram.chat_id`; it does not accept `@usernames`, topic IDs, or a separate
topic setting.

Set the token without placing it in shell history:

```sh
printf 'Telegram bot token: '
read -r -s TELEGRAM_BOT_TOKEN
printf '\n'
export TELEGRAM_BOT_TOKEN
```

For repeated use, put `export TELEGRAM_BOT_TOKEN="..."` in a private shell
configuration file such as `~/.zshrc` or `~/.bashrc`, then `chmod 600` that
file. This is convenient but stores the token as plaintext; rotate it through
BotFather immediately if it is exposed.

#### Find a numeric chat ID safely

After adding the bot, send one fresh event. In a default privacy-mode group,
ordinary messages are hidden from the bot, so send a relevant command such as
`/start@BOT_USERNAME` (replace `BOT_USERNAME`) or another bot-directed command;
see Telegram's [privacy-mode guide](https://core.telegram.org/bots/features#privacy-mode).
In a channel, publish a fresh channel post. With `TELEGRAM_BOT_TOKEN` already
exported, this standard-library snippet prints only the numeric chat ID, type,
and title/name from `getUpdates`; it never prints the raw response or
token-bearing URL:

```sh
python3 - <<'PY'
import json, os
from urllib.request import Request, urlopen

try:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    request = Request(
        f"https://api.telegram.org/bot{token}/getUpdates",
        headers={"Accept": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        data = json.loads(response.read())
except (KeyError, OSError, ValueError, json.JSONDecodeError):
    raise SystemExit("getUpdates request failed; check token, network, and webhook state")
if not isinstance(data, dict) or data.get("ok") is not True or not isinstance(data.get("result"), list):
    raise SystemExit("getUpdates returned an invalid or unsuccessful response")
seen = set()
for update in data.get("result", []):
    if not isinstance(update, dict):
        continue
    for field in ("message", "channel_post", "my_chat_member"):
        item = update.get(field)
        chat = item.get("chat") if isinstance(item, dict) else None
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        if (not isinstance(chat_id, int) or isinstance(chat_id, bool)
                or chat_id in seen):
            continue
        chat_type = chat.get("type")
        if not isinstance(chat_type, str):
            continue
        seen.add(chat_id)
        title = chat.get("title") or chat.get("first_name") or ""
        print(chat_id, chat_type, title if isinstance(title, str) else "")
PY
```

An `ok: true` response with an empty result means there are no fresh visible
updates; send the event again and rerun the snippet. An API error can mean an
invalid token, a webhook conflict, or another Bot API problem. `getUpdates`
cannot be used while a webhook is active; see the official
[`getUpdates` documentation](https://core.telegram.org/bots/api#getupdates) for
the supported update modes. Put the resulting integer in `kakera.json`, for
example `"chat_id": -1001234567890`; channel and supergroup IDs commonly have
the `-100` prefix. For a private chat with the bot, the printed chat ID is the
user ID for `telegram.allowed_user_ids`.

#### Telegram Intake

`kakera telegram` reads private messages to the bot from allowed users. Each
recognized Instagram, Twitter/X, Reddit, or RedNote URL becomes a Transient
Telegram Delivery to `telegram.chat_id`. Nothing is stored. Video is allowed.
A later DM with the same URL sends again. Group messages, message edits, and
users not on the allowlist are ignored.

```sh
./kakera telegram
./kakera telegram --watch
./kakera telegram --browser safari --watch
```

Without `--watch`, pending updates are drained and the process exits. `--watch`
long-polls until Ctrl-C. `--interval` is rejected. `--browser`, `--account`, and
`--twitter-account` are the only source-auth options.

A DM with two Source Service URLs is two independent Deliveries. Unsupported
URLs are ignored. Success is the group Delivery; Kakera does not reply in the
DM on success. Failure replies in that private chat; retry by sending a new
message. The group caption names the sender with `@username`, or their display
name if they have no username — never the numeric user ID:

```text
Post title - Instagram
https://www.instagram.com/p/ABC/
from @imjma
```

Inbox and Todoist Queue Reports are sent in the private chat of
`telegram.report_user_id`. The queue watcher sends that text itself; it does
not go through `kakera telegram --watch`, and a second Intake watcher is still
refused. Identical failures are not re-reported in this watch until the reason changes
or a later capture succeeds. Missing `report_user_id` or Telegram configuration
skips the report without changing capture behavior. Named Instagram Failed
Sources (`Instagram session expired` and `Instagram post is followers-only`)
are one report per class listing only URLs not already sent in this watch,
even when another source in the group saved. A restart treats still-failing
items as new. A watcher that cannot read Inbox or Todoist logs the error and
does not send a Queue Report. Intake replies to the sender with the same
reason.

The last consumed Telegram `update_id` is stored in `kakera.telegram-state.json`
next to `kakera.json` (gitignored). A second `kakera telegram --watch` fails
immediately. A webhook on this bot token refuses to start; delete it before
Intake. `getUpdates` cannot run while a webhook is active.

#### Automatic delivery

`--share telegram` is shorthand for adding the current-request tag `share/telegram`:

```sh
./kakera --share telegram https://www.instagram.com/p/ABC/
./kakera local --share telegram https://x.com/user/status/123
./kakera --compose --share telegram URL1 URL2
./kakera --tag share/telegram URL
```

The note and local attachments are saved first. For configured automatic
captures, both are inside the configured Obsidian vault. For `local --share telegram`,
the note and images stay in Kakera's local `downloads/` and `attachments/`
output roots. A Telegram failure reports the capture as saved but returns
failure, so an Inbox or Todoist task remains pending for retry. The persisted
`share/telegram` tag alone never retriggers an old note on recapture. A successful
receipt is idempotent per current chat, so later captures and watch polls report
the existing delivery without sending it again.

For queues, put `#share/telegram` on an unchecked Inbox task or apply the native
`share/telegram` label in Todoist:

```md
- [ ] #share/telegram https://www.instagram.com/p/ABC/
```

```sh
./kakera inbox --tag share/telegram
./kakera inbox --tag share/telegram --watch
./kakera todoist --tag share/telegram
./kakera todoist --tag share/telegram --watch
```

Invocation tags apply to every group in that run and later watch polls. A
queue item is checked/closed only when capture succeeds; Telegram failure
keeps it pending.

`share/telegram-only` on an Inbox hashtag, a Todoist label, or `--tag` for
`inbox`/`todoist` requests a Transient Telegram Delivery instead: each URL is
sent independently, nothing is stored, and there is no Share Receipt. The group
is checked/closed only when every URL delivers; any failure keeps it pending.
If both tags are present, `share/telegram-only` wins. Direct URL captures still
use `--share telegram-only`.

```md
- [ ] #share/telegram-only https://www.instagram.com/p/ABC/
```

```sh
./kakera inbox --tag share/telegram-only
./kakera todoist --tag share/telegram-only --watch
```

#### Publish existing notes

`kakera share telegram` is the manual path for notes already in the configured
`obsidian.notes` folder; its note must be in that folder and its selected images
must resolve inside the configured vault. It does not recapture the source or
add the `share/telegram` tag. `kakera telegram` without `share` is Intake, not
this command.

```sh
./kakera share telegram https://www.instagram.com/p/ABC/
./kakera share telegram "Folder/Note.md"
./kakera share telegram "/absolute/path/inside/the/notes/folder/Note.md"
./kakera share telegram "Note title"
./kakera share telegram "[[Folder/Note|display alias]]"
./kakera share telegram "Note A" "Folder/Note B.md"
```

Selectors are exact: source URL, notes-relative path, an absolute path only
when it resolves inside the configured notes folder, or a unique filename/stem.
Bare wikilinks such as `[[Note]]` and `[[Note.md]]` also require a unique
filename/stem; qualified wikilinks such as `[[Folder/Note]]` select that exact
relative path. Ambiguity lists the matching relative paths. Multiple selectors
continue independently and return failure if any selector fails.

The first manual send proceeds immediately. If the current chat already has a
receipt, Kakera asks on stderr with `[y/N]`; only `y` or `yes` confirms. Enter,
`n`, EOF, or a non-interactive terminal declines or fails safely. There is no
`--resend`, `--yes`, or `--force` bypass.

#### Media, captions, and receipts

For manual note publishing, Kakera reads local Markdown images such as
`![](../attachments/photo.jpg)` and Obsidian embeds such as
`![[attachments/photo.jpg]]` in document order. It resolves and deduplicates
real paths inside the configured vault. Automatic captures use their configured
vault or, for `local --share telegram`, Kakera's local output roots. Remote URLs,
`data:` URLs, missing files, non-images, escaping paths, and ambiguous bare
image names are not fetched or sent. Files over 10 MB are skipped, and the
first ten eligible images are sent. A single image uses `sendPhoto`; two
through ten use `sendMediaGroup`. Transient URL delivery also accepts MP4
video (50 MB limit): one video uses `sendVideo`; mixed photos and videos use
`sendMediaGroup` in source order.

The caption contains only the actual note filename stem on line one for an
existing note, or the prospective Source Note name derived from fetched
metadata and service for a transient URL. The transient name is only a
caption value; no Source Note is created. When the complete caption fits
Telegram's 1,024-character limit, one source URL appears on line two. A
Telegram Intake Delivery then adds `from @username` or `from Display Name` on
the next line. It does not include note body text, title fields, tags, or
follow-up text.

Capture, note, and `--share telegram-only` captions look like:

```text
Post title - Instagram
https://www.instagram.com/p/ABC/
```

A Telegram Intake Delivery adds the sender:

```text
Post title - Instagram
https://www.instagram.com/p/ABC/
from @imjma
```

After acknowledgement, the note stores the latest message IDs per chat in the
`kakera.shared.telegram` namespace:

```yaml
kakera: {"shared":{"telegram":{"-1001234567890":[456,457],"-1009876543210":[88]}}}
```

Resends replace only the current chat's IDs; receipts for other chats remain.
Telegram acknowledgement and local receipt writeback are separate operations,
so a timeout or write failure can leave delivery uncertain. Kakera reports that
partial state instead of silently claiming success.

#### No-storage publishing

`kakera share telegram-only SELECTOR...` uses the same exact selectors, note-folder
boundary, vault-contained local embeds, image limits, and caption rules as the
receipt-aware `share telegram` command. It never reads receipts, prompts, changes
notes, or creates a receipt, so every invocation sends again. It accepts no
capture, queue, account, cookie, or OAuth options.

`kakera --share telegram-only URL...` validates the Telegram configuration and token,
fetches each URL completely into one private temporary directory, and sends the
first ten eligible local images and MP4 videos, in source order. It creates no
note, attachment, output folder, tag, queue state, or receipt; temporary files
exist briefly on disk and can remain after a crash. A partial or failed fetch is
never sent. Each URL is an independent post, and a transport timeout is reported
as uncertain: inspect the Telegram target before retrying.

For this command, `--browser`, `--account`, and `--twitter-account` are the only
source-auth options. Capture/composition flags, explicit output folders, tags,
Inbox/Todoist/`--watch`, cookie exports, and OAuth are rejected. It must not be
used as a fallback for either note-publishing command.

`--share telegram-only` / queue `share/telegram-only` / Telegram Intake can
publish MP4 video from Instagram, Twitter/X, Reddit, and RedNote. Capture,
`share/telegram`, and note-publishing commands stay image-only and do not
download video. These no-storage modes do not compose sources, process
Inbox/Todoist/watch, or fetch remote images embedded in an existing note.

#### Telegram troubleshooting

| Symptom | Check |
| --- | --- |
| `TELEGRAM_BOT_TOKEN is not set` | Export the token in the shell running Kakera; never add it to JSON. |
| Invalid `telegram.chat_id` | Use the exact numeric ID from `getUpdates`, including its minus sign; no `@username` or topic ID. |
| Invalid `telegram.allowed_user_ids` | Non-empty array of numeric user IDs; required for `kakera telegram`. A private-chat ID from the snippet above is the user ID. |
| Invalid `telegram.report_user_id` | A single numeric user ID listed in `allowed_user_ids`; omit the key to skip Inbox/Todoist Queue Reports. |
| Bot cannot send | Recheck channel **Post Messages** or group send-message/send-photo permissions. |
| `getUpdates` is empty | Send a fresh group/channel event or DM; check whether another consumer or webhook has consumed updates. |
| `Telegram webhook is set` | Delete the webhook on this bot token; Intake uses `getUpdates` only. |
| `telegram intake already running` | Another `kakera telegram` process holds the Intake lock. |
| `Telegram getUpdates conflict` | Another process is calling `getUpdates` on this bot token (a second watch, another machine, or the chat-ID snippet). Stop the other consumer. |
| `Telegram request failed: …` | Telegram returned that description; the token is valid enough to talk to the API, but this call was rejected. |
| `PHOTO_INVALID_DIMENSIONS` | Telegram rejects photos whose width+height exceed 10,000 or whose aspect ratio exceeds 20:1. Kakera skips those images, sends the rest, and keeps the full files in the Capture. |
| Existing-note has no eligible images | Check Markdown/Obsidian embed paths, image type, vault containment, and the 10 MB limit. Remote, data, missing, ambiguous, and outside-vault images are omitted. |
| Transient URL has no eligible images or video | Check that the complete fetch produced supported local images or an MP4; partial/failed fetches are never sent. WebM and other non-MP4 video is ignored. |
| Ambiguous note or image | Use the listed relative note path or a qualified embed/path. |
| Telegram timeout/API failure | First inspect the target in Telegram to see whether the images arrived; only retry when delivery is confirmed absent or a retry is appropriate, because acknowledgement may have been lost after Telegram accepted the send. |
| Telegram sent but receipt update failed | Inspect the note for concurrent edits or filesystem/provider write errors; resend only after confirming Telegram state. |
| Queue remains unchecked/open | This is intentional when capture or Telegram delivery failed; correct the cause and rerun. |

The `reddit` section is added by `reddit-oauth`. `kakera.json`,
`kakera.telegram-state.json`, `.cookies/`, `downloads/`, and `attachments/` are
ignored by Git. Capture coordination locks
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
failed. If no image is saved, the task remains open and, when
`telegram.report_user_id` is set, the failure is reported in that user's
private chat.
`--watch` polls every 3 minutes. Override with `--interval` or
`todoist.interval`. Native Todoist labels on the parent and nested subtasks become
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
- [ ] http://xhslink.com/m/...
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
no image is saved, the group remains unchecked and, when
`telegram.report_user_id` is set, the failure is reported in that user's
private chat.
`--watch` rechecks the file every 2 seconds and stops with Ctrl-C. Override with
`--interval` or `obsidian.interval`. Add `--tag` to apply
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
| Instagram | `https://www.instagram.com/p/ID/`, `/reel/ID/`, `/reels/ID/`, `/tv/ID/` | Uses gallery-dl; login cookies may be required. `/reels/` is the same post as `/reel/`. Capture keeps images only. Transient Telegram Delivery can send MP4 video. |
| Twitter/X | `https://x.com/USER/status/ID`, `https://x.com/i/web/status/ID`, `twitter.com/.../status/ID`, optional `/photo/N` or `/video/N` | Individual status only; Capture saves images and ignores videos. Transient Telegram Delivery can send MP4 video. |
| Reddit | `https://redd.it/ID`, Reddit `/comments/ID/` or `/gallery/ID/` URLs, app share `/r/SUB/s/CODE` | Uses gallery-dl with the Reddit OAuth API; configure `reddit.client_id` / `user_agent` and run `reddit-oauth` when required. Capture keeps images only. A `/s/` share link is resolved to the post before fetch. Transient Telegram Delivery can send MP4 video. |
| RedNote | `http(s)://xhslink.com/m/ID` or `/o/ID`, public `xiaohongshu.com/explore/ID` or `/item/ID` | Reads public page data and images; no login-only posts. Capture does not download video; Transient Telegram Delivery sends covers then the MP4. |

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
  in mixed posts are ignored on Capture. `--share telegram-only` and Telegram
  Intake can send the MP4.
- RedNote is limited to public page data and has a 5 MB page and 50 MB per-image
  or per-video limit. Gallery-dl captures are limited to 50 MB per file.
  Capture does not download RedNote video; Transient Telegram Delivery does.
- Kakera does not bypass login walls, CAPTCHAs, private-post restrictions, or
  access controls, and native iOS/macOS apps are not part of v0.1.0.
- Kakera processes only URLs you provide, plus Source Service URLs in allowed
  users' private Telegram messages when Telegram Intake is running. For an explicitly requested Telegram
  delivery, it sends the selected image bytes and, for Transient Telegram
  Delivery, MP4 video, the actual note stem (or a
  transient source-derived prospective name), optional source URL, and for
  Telegram Intake the sender's `@username` or display name to
  `api.telegram.org`; it does not send source text, tags, browser cookies,
  numeric Telegram user IDs, or service account tokens as Telegram message
  content.
- Kakera has no Kakera-hosted relay service: capture extraction uses the
  configured source tools, and Telegram delivery goes directly to Telegram's
  Bot API. Remote note images are never downloaded for Telegram.

Only save or distribute content you have permission to retain and share.

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
