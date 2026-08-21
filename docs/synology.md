# Synology NAS

This deployment runs Kakera's Todoist and Telegram watchers beside Obsidian Headless Sync in Synology Container Manager. It exposes no network ports.

## Before cutover

1. On the current authoritative Obsidian device, wait for Sync to finish, pause edits, make a complete independent versioned backup of the vault, and verify that several notes and attachments can be read from that backup. Do not rely on Obsidian Sync history as this checkpoint.
2. Stop every existing `kakera todoist --watch` and `kakera telegram --watch`. Only the NAS may run them after cutover.
3. Create an empty, dedicated Btrfs shared folder for the vault. After the initial sync is verified below, [Snapshot Replication](https://kb.synology.com/en-us/DSM/tutorial/How_can_I_recover_files_from_snapshots) will protect this share hourly with seven-day retention. A separate Hyper Backup destination is still needed to cover NAS or volume loss.
4. Do not run Synology Drive, Cloud Sync, or another filesystem sync engine against this vault.

[Obsidian Headless Sync](https://obsidian.md/help/sync/headless) is open beta. This deployment pins `obsidian-headless` 0.0.14; review its [changelog](https://github.com/obsidianmd/obsidian-headless/blob/master/CHANGELOG.md) and take a snapshot before changing that pin.

## Prepare the project

SSH to the NAS, enter this directory, and create the private runtime files:

```sh
cd /path/to/kakera/deploy/synology
cp .env.example .env
mkdir -p .cookies config obsidian-config state/tmp
cp kakera.example.json config/kakera.json
chmod 600 .env config/kakera.json
chmod 700 .cookies config obsidian-config state state/tmp
```

Edit `.env` with the Synology account's numeric UID/GID (`id USERNAME`), dedicated shared-folder path, and Todoist and Telegram tokens. Edit `config/kakera.json` with the project, chat, allowed-user IDs, and the aliases of the cookie exports you copy into `.cookies/`. Keep `browser` as `null`; a NAS container has no browser session.

All runtime directories and the vault must be owned by the configured UID/GID. Cookie files are bearer credentials; copy the existing exports, set mode `600`, and never commit them.

Build the pinned images:

```sh
docker compose --profile workers build
```

If Reddit OAuth is required, authorize it once after the build. Its refresh token persists under `state/`, and Kakera updates the private deployment config:

```sh
docker compose --profile workers run --rm --no-deps todoist --reddit-oauth YOUR_REDDIT_USERNAME
```

## Link and verify Obsidian Sync

These commands are intentionally interactive, so the Obsidian password, MFA code, auth token, and optional vault encryption password never enter Compose or Git:

```sh
docker compose run --rm obsidian login
docker compose run --rm obsidian sync-list-remote
docker compose run --rm obsidian sync-setup --vault "YOUR REMOTE VAULT NAME" --path /vault --device-name synology-kakera
docker compose run --rm obsidian sync-config --path /vault --mode bidirectional --conflict-strategy merge --file-types image,audio,video,pdf,unsupported --excluded-folders "" --configs "" --device-name synology-kakera
docker compose run --rm obsidian sync --path /vault
docker compose run --rm obsidian sync-status --path /vault
```

Do not start any continuous service unless the one-time sync succeeds, the expected folders, notes, and attachments are present, and `sync-status` reports bidirectional mode with no excluded folders and no configuration categories. Browse representative files, then take the first Snapshot Replication snapshot of the complete NAS vault. This is the recoverable post-sync checkpoint. Obsidian authentication and sync databases persist in `obsidian-config/`.

Start continuous sync, inspect its first log, then enable both workers:

```sh
docker compose up -d obsidian
docker compose logs --tail 100 obsidian
docker compose --profile workers up -d
docker compose --profile workers ps
```

The shared `state/` directory persists the Telegram update offset, Reddit OAuth cache, and Kakera's cross-process capture locks. Both services restart automatically unless explicitly stopped.

## Operations

```sh
docker compose --profile workers logs -f
docker compose --profile workers ps
docker compose --profile workers restart todoist telegram
docker compose --profile workers down
```

For updates, stop the workers, take a vault snapshot, change the pinned image or package version deliberately, rebuild, run a one-time sync and status check, then start the workers again. Restore individual damaged files from Snapshot Replication; restore the NAS or volume from the independent backup.
