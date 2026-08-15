# Kakera

Kakera saves images and source metadata from Instagram, Reddit, and public RedNote posts as portable Markdown notes. Output works as ordinary local files or directly inside an Obsidian vault.

## Requirements

- macOS
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- A browser session logged into Instagram when Instagram requires authentication

`gallery-dl` is declared inside `kakera.py` and installed automatically by `uv`.

## Setup

```sh
git clone YOUR_GITHUB_URL
cd kakera
cp kakera.example.json kakera.json
```

Edit `kakera.json` with your browser name and Obsidian vault path. Common browser names include `safari`, `chrome`, `firefox`, `brave`, `edge`, and `orion`.

Authorize Reddit once with gallery-dl's registered app:

```sh
./kakera reddit-oauth YOUR_REDDIT_USERNAME
```

After authorization, gallery-dl keeps the refresh token in its local cache and Kakera automatically adds the non-secret Reddit client ID and user-agent to `kakera.json`. Never publish the refresh token printed by gallery-dl.

### Todoist inbox on iOS (optional)

Create a Todoist project for Kakera, put its ID in `todoist.project_id` in
`kakera.json`, then copy your personal API token from Todoist's developer
settings and export it on the machine running Kakera:

```sh
export TODOIST_API_TOKEN="YOUR_TODOIST_API_TOKEN"
```

Follow the [Todoist iOS Shortcut setup](docs/todoist.md) to send shared URLs
directly to Todoist's API without opening the Todoist app.

Run from the project folder:

```sh
./kakera instagram-cookies personal  # save the current Orion Instagram session
./kakera URL        # use the configured browser's current cookies
./kakera --account work URL  # use the saved "work" account cookies
./kakera local URL  # save inside this repository
./kakera inbox      # process the configured Obsidian inbox
./kakera inbox --watch  # keep processing the inbox until Ctrl-C
./kakera todoist   # process open tasks in the configured Todoist project
./kakera todoist --watch  # keep polling Todoist until Ctrl-C
./kakera reddit-oauth USERNAME  # authorize Reddit and update kakera.json
```

Optional global command:

```sh
mkdir -p ~/.local/bin
ln -s "$PWD/kakera" ~/.local/bin/kakera
```

Ensure `~/.local/bin` is in your `PATH`, then use `kakera` from any folder.

## Configuration

```json
{
  "browser": "orion",
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

For multiple Instagram accounts, switch to an account in Orion and run
`./kakera instagram-cookies ALIAS` once for each one. Cookie files stay local in
`.cookies/` with owner-only permissions. Set the default alias under `instagram.account`,
for example `"instagram": {"account": "personal"}`, or choose one per run with
`--account ALIAS`. If no account is configured or passed, Kakera reads the
configured browser's cookies directly (`"browser": "orion"` uses Orion).
Repeat the save command when a saved session expires.

The `reddit` section is added by `kakera reddit-oauth`. `kakera.json` is ignored by Git so personal paths are not published.

Set `todoist.project_id` to the Todoist project ID and export a Todoist API token
as `TODOIST_API_TOKEN`. `kakera todoist` reads open tasks in that project,
captures the first HTTP(S) URL in each task's content or description, and closes
the task only after a successful capture. Tasks without a URL or with failed
captures remain open. Use `kakera todoist --watch` to retry them while polling.

If an iCloud-backed inbox reports `Operation not permitted` even after granting
your terminal Full Disk Access, the Obsidian app-specific iCloud container may
be blocking command-line access. Use a local vault or a normal iCloud Drive
path visible in Finder instead; changing Python or uv permissions will not
fix that File Provider restriction.

## Inbox workflow

Add unchecked URL tasks to the configured inbox from Obsidian on iOS:

```md
- [ ] https://www.instagram.com/p/...
- [ ] https://redd.it/...
- [ ] http://xhslink.com/o/...
```

Run `kakera inbox` on the Mac. Successful tasks become `[x]`; failures remain unchecked.
Run `kakera inbox --watch` to process existing tasks and keep watching for new ones.
Failed tasks retry after the inbox changes or the command restarts. Stop with Ctrl-C.

## Output

```text
kakera/
  Post title - Instagram.md
attachments/
  instagram/
    instagram-POST_ID-01.jpg
```

Notes contain Obsidian properties, source text when available, and relative image links. Existing images are deduplicated by content hash.

If another post already uses the same title, Kakera keeps both notes by naming the later one `Post title - POST_ID - Service.md`.

## Limitations

- Instagram may require cookies from a logged-in browser and may reject or rate-limit extraction.
- Reddit and Instagram can change without notice; Kakera depends on `gallery-dl` extractors.
- RedNote uses public page data and may break when its website changes; login-only posts are unsupported.
- Video, private-post login flows, and native iOS/macOS apps are not part of v0.1.0.
- Kakera processes only URLs you provide and does not bypass login walls, CAPTCHAs, or access controls.

Only save content you have permission to retain. Kakera runs locally and does not send URLs, cookies, or media to a Kakera service.

## Development

```sh
python3 test_kakera.py
./kakera --version
```

The test uses only Python's standard library and does not contact social sites.

## License

[MIT](LICENSE)
