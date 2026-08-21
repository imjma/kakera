# Synology NAS

This deployment runs Kakera's Todoist and Telegram watchers beside Obsidian Headless Sync in Synology Container Manager. It exposes no network ports.

## Before cutover

1. On the current authoritative Obsidian device, wait for Sync to finish, pause edits, make a complete independent versioned backup of the vault, and verify that several notes and attachments can be read from that backup. Do not rely on Obsidian Sync history as this checkpoint.
2. Stop every existing `kakera todoist --watch` and `kakera telegram --watch`. Only the NAS may run them after cutover.
3. Create an empty, dedicated Btrfs shared folder for the vault. After the initial sync is verified below, [Snapshot Replication](https://kb.synology.com/en-us/DSM/tutorial/How_can_I_recover_files_from_snapshots) will protect this share hourly with seven-day retention. A separate Hyper Backup destination is still needed to cover NAS or volume loss.
4. Do not run Synology Drive, Cloud Sync, or another filesystem sync engine against this vault.

[Obsidian Headless Sync](https://obsidian.md/help/sync/headless) is open beta. This deployment pins `obsidian-headless` 0.0.14; review its [changelog](https://github.com/obsidianmd/obsidian-headless/blob/master/CHANGELOG.md) and take a snapshot before changing that pin.

## Prepare the project

### 1. Choose the NAS account

A new Synology account is not required. The simplest choice is an existing non-root account that can read and write the Obsidian shared folder. A dedicated `kakera` account is optional; it needs access only to the vault and Kakera runtime directories, not SSH, administrator, or Container Manager permissions.

SSH to the NAS with an administrator-capable account and inspect the selected runtime account:

```sh
id YOUR_USERNAME
```

For output such as:

```text
uid=1026(jma) gid=100(users) groups=100(users),101(administrators)
```

use `PUID=1026` and `PGID=100`. Use the primary `uid=` and `gid=` values, not an additional group such as `administrators`. Docker uses these numbers even though the image does not contain the Synology username.

### 2. Put the checkout on the NAS

Keep the checkout separate from the vault:

```text
/volume1/docker/kakera/        Kakera source and private deployment files
/volume1/obsidian/             snapshot-enabled Synology shared folder
/volume1/obsidian/personal/    local copy of the Personal Obsidian vault
```

Do not put the checkout inside the vault. For a GitHub-hosted repository:

```sh
cd /volume1/docker
git clone YOUR_GITHUB_REPOSITORY_URL kakera
cd /volume1/docker/kakera/deploy/synology
```

For unpushed local changes, run `rsync` from the development computer instead. The exclusions preserve NAS credentials and runtime state:

```sh
rsync -az \
  --exclude='.git/' \
  --exclude='kakera.json' \
  --exclude='.cookies/' \
  --exclude='deploy/synology/.env' \
  --exclude='deploy/synology/config/' \
  --exclude='deploy/synology/.cookies/' \
  --exclude='deploy/synology/state/' \
  --exclude='deploy/synology/obsidian-config/' \
  /path/to/kakera/ \
  YOUR_USERNAME@YOUR_NAS_IP:/volume1/docker/kakera/
```

### 3. Create the private runtime files

On the NAS:

```sh
cd /volume1/docker/kakera/deploy/synology
cp .env.example .env
mkdir -p .cookies config obsidian-config state/tmp
cp kakera.example.json config/kakera.json
chmod 600 .env config/kakera.json
chmod 700 .cookies config obsidian-config state state/tmp
```

Make the runtime account the owner, substituting the numbers returned by `id`:

```sh
KAKERA_UID=1026
KAKERA_GID=100
sudo chown -R "$KAKERA_UID:$KAKERA_GID" .env .cookies config obsidian-config state
```

In DSM, open **Control Panel → Shared Folder → obsidian → Edit → Permissions** and grant the same account read/write access. Then create a vault subfolder owned directly by the runtime UID/GID:

```sh
sudo mkdir -p /volume1/obsidian/personal
sudo chown "$KAKERA_UID:$KAKERA_GID" /volume1/obsidian/personal
sudo chmod 700 /volume1/obsidian/personal
```

This subfolder matters on Synology. The shared-folder root can appear as `root:root` with ACL-only permissions, and an SSH administrator may be able to write through its supplementary `administrators` group. A container launched as `PUID:PGID` does not inherit that supplementary group. Mounting the directly owned `personal/` subfolder avoids granting the container administrator access.

### 4. Edit `.env`

Open the file with `vi .env` or Synology Text Editor. In `vi`, press `i`, edit, press `Esc`, type `:wq`, and press Enter.

```dotenv
PUID=1026
PGID=100
TZ=Australia/Sydney
VAULT_PATH=/volume1/obsidian/personal
TODOIST_API_TOKEN=YOUR_REAL_TODOIST_TOKEN
TELEGRAM_BOT_TOKEN=YOUR_REAL_TELEGRAM_BOT_TOKEN
```

- `PUID` and `PGID` are the numeric values from `id`.
- `VAULT_PATH` is the real NAS shared-folder path. Inside the containers it becomes `/vault`.
- `TODOIST_API_TOKEN` is the personal API token from Todoist settings.
- `TELEGRAM_BOT_TOKEN` is the token issued by Telegram's `@BotFather`.
- Do not add spaces around `=` or commit this file.

Confirm the file is private without printing its contents:

```sh
ls -l .env
```

It should start with `-rw-------` and show the selected runtime account as owner.

### 5. Edit `config/kakera.json`

If Kakera already works on another computer, copy its existing configuration to the NAS and then change the browser and vault fields:

```sh
scp /path/to/kakera/kakera.json \
  YOUR_USERNAME@YOUR_NAS_IP:/volume1/docker/kakera/deploy/synology/config/kakera.json
```

After copying, restore the runtime ownership and private mode on the NAS:

```sh
cd /volume1/docker/kakera/deploy/synology
sudo chown "$KAKERA_UID:$KAKERA_GID" config/kakera.json
sudo chmod 600 config/kakera.json
```

Otherwise edit the generated example with `vi config/kakera.json`. The NAS configuration should resemble:

```json
{
  "browser": null,
  "instagram": {
    "account": "personal"
  },
  "twitter": {
    "account": "personal"
  },
  "obsidian": {
    "vault": "/vault",
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
    "allowed_user_ids": [123456789]
  }
}
```

Keep `browser` as `null`; a NAS container has no browser session. Keep `obsidian.vault` as `/vault`, not the host path. `telegram.chat_id` is the delivery destination, while `allowed_user_ids` contains the individual Telegram users permitted to submit private messages. Follow the main README's Telegram setup if these IDs are not already known.

The account aliases must match the cookie filenames exactly. For example:

```text
.cookies/instagram-personal.txt  →  "instagram": {"account": "personal"}
.cookies/twitter-personal.txt    →  "twitter": {"account": "personal"}
```

Remove an `instagram` or `twitter` block if that authenticated service is not needed; do not leave a placeholder alias.

### 6. Copy browser cookie exports

Run these commands from the computer where the cookie files already exist:

```sh
scp /path/to/kakera/.cookies/instagram-personal.txt \
  YOUR_USERNAME@YOUR_NAS_IP:/volume1/docker/kakera/deploy/synology/.cookies/
scp /path/to/kakera/.cookies/twitter-personal.txt \
  YOUR_USERNAME@YOUR_NAS_IP:/volume1/docker/kakera/deploy/synology/.cookies/
```

Then protect them on the NAS:

```sh
cd /volume1/docker/kakera/deploy/synology
sudo chown "$KAKERA_UID:$KAKERA_GID" .cookies/*.txt
sudo chmod 600 .cookies/*.txt
```

Cookie files are bearer credentials. Never commit or share them.

### 7. Validate and build

Validate Compose without displaying the expanded tokens:

```sh
sudo docker compose --profile workers config --quiet
```

No output and exit status zero means the configuration is valid. Build the pinned images:

```sh
sudo docker compose --profile workers build
```

Synology commonly restricts `/var/run/docker.sock`, so the examples use `sudo`. Do not change `PUID` or `PGID`; Compose still runs the services as the selected runtime account. Do not make the Docker socket world-writable.

First verify that the NAS account can write to the host vault without `sudo`:

```sh
touch /volume1/obsidian/personal/.kakera-host-test
rm /volume1/obsidian/personal/.kakera-host-test
```

Then open a shell as the container's configured UID/GID:

```sh
sudo docker compose --profile workers run --rm --no-deps --entrypoint sh todoist
```

At its `$` prompt, run each command separately:

```sh
touch /vault/.test
rm /vault/.test
echo "vault writable"
exit
```

Continue only when neither file operation reports `Permission denied`.

If Reddit OAuth is required, authorize it once after the build. Its refresh token persists under `state/`, and Kakera updates the private deployment config:

```sh
sudo docker compose --profile workers run --rm --no-deps todoist --reddit-oauth YOUR_REDDIT_USERNAME
```

## Link and verify Obsidian Sync

These commands are intentionally interactive, so the Obsidian password, MFA code, auth token, and optional vault encryption password never enter Compose or Git:

```sh
sudo docker compose run --rm obsidian login
sudo docker compose run --rm obsidian sync-list-remote
sudo docker compose run --rm obsidian sync-setup --vault "Personal" --path /vault --device-name synology-kakera
sudo docker compose run --rm obsidian sync-config --path /vault --mode bidirectional --conflict-strategy merge --file-types image,audio,video,pdf,unsupported --excluded-folders "" --configs "" --device-name synology-kakera
sudo docker compose run --rm obsidian sync --path /vault
sudo docker compose run --rm obsidian sync-status --path /vault
```

Replace `Personal` if linking a different remote vault. Do not start any continuous service unless the one-time sync reports `Fully synced`, the expected folders, notes, and attachments are present, and `sync-status` reports bidirectional mode and no configuration categories. Browse representative files, then open **Snapshot Replication → Snapshots → obsidian → Snapshot → Take a Snapshot** in DSM. This is the recoverable post-sync checkpoint. Obsidian authentication and sync databases persist in `obsidian-config/`.

### Sync only Kakera folders

The pinned Headless Sync CLI cannot express “include only `kakera/` and `attachments/`.” It syncs the vault by default and supports only a comma-separated `--excluded-folders` list. To limit an existing NAS client, stop all three services, take a snapshot, and explicitly name every other top-level vault folder:

```sh
sudo docker compose --profile workers stop
sudo docker compose run --rm obsidian sync-config --path /vault --mode bidirectional --conflict-strategy merge --file-types image,audio,video,pdf,unsupported --excluded-folders "Projects,Archive,Daily Notes" --configs "" --device-name synology-kakera
sudo docker compose run --rm obsidian sync-status --path /vault
sudo docker compose run --rm obsidian sync --path /vault
sudo docker compose --profile workers up -d
```

Replace the example exclusions with every folder except `kakera` and `attachments`. This is not a durable allowlist: vault-root files and any new unlisted folder will still sync. Excluding a folder after it has synced does not remove its existing remote copy. For strict two-folder isolation, use a separate Obsidian remote vault containing only those folders; otherwise full-vault sync is simpler and safer. See the official [Headless Sync options](https://github.com/obsidianmd/obsidian-headless#ob-sync-config) and the open [included-folders request](https://github.com/obsidianmd/obsidian-headless/issues/37).

Start continuous sync, inspect its first log, then enable both workers:

```sh
sudo docker compose up -d obsidian
sudo docker compose logs --tail 100 obsidian
sudo docker compose --profile workers up -d
sudo docker compose --profile workers ps
sudo docker compose --profile workers logs --tail 100 todoist telegram
```

The shared `state/` directory persists the Telegram update offset, Reddit OAuth cache, and Kakera's cross-process capture locks. Both services restart automatically unless explicitly stopped. Empty Todoist and Telegram logs are normal while both watchers are idle.

Test Telegram Intake by sending the bot a private message from an allowed user containing a supported Source Service URL. Success appears in the configured destination chat and does not reply to the private message. Test Todoist by adding a task containing a supported URL to the configured project; within the configured interval it should create a note and attachment, sync them to another Obsidian device, and close the task.

## Operations

```sh
sudo docker compose --profile workers logs -f
sudo docker compose --profile workers ps
sudo docker compose --profile workers restart todoist telegram
sudo docker compose --profile workers down
```

For updates, stop the workers, take a vault snapshot, change the pinned image or package version deliberately, rebuild, run a one-time sync and status check, then start the workers again. Restore individual damaged files from Snapshot Replication; restore the NAS or volume from the independent backup.
