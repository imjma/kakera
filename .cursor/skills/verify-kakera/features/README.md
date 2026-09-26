# Kakera verification map

This directory is the maintained source for verifying the user-facing behavior of Kakera. Read the index before driving the CLI, then use the matching feature file as the recipe.

The primary surface is the `./kakera` CLI. Inbox is a second command on that CLI. Telegram, Todoist, cookie export, Reddit OAuth, and the Synology stack are outside this map: they need live credentials or they rewrite config and cookies.

## Baseline preconditions

- Launch from the repository root with the isolated `$KAKERA_CONFIG`, `$VERIFY_ROOT`, and `$SESSION` in `.cursor/skills/verify-kakera/SKILL.md`.
- Run `doctor` and require the line `ok: kakera verify doctor`.
- Keep `TELEGRAM_BOT_TOKEN` and `TODOIST_API_TOKEN` unset.
- Do not point `KAKERA_CONFIG` at the checkout `kakera.json` or at a real Obsidian vault.
- Never drive a tmux session that was not started by this verification run.
- Do not run `make restart`.

## Driving conventions

- Start every recipe from the baseline state unless its preconditions say otherwise.
- Treat every command as literal. Keep quoted URLs, tags, and flags unchanged.
- Send each command into `$SESSION` and wait for `KAKERA_VERIFY_DONE`.
- Restore nothing in the checkout: refusal drives must leave git status empty.
- Do not remove proof artifacts during cleanup. Proof files stay in `/cursor/stores/bc-62ef6805-149a-4c9a-95c1-24137bf3b93c/media/verify-kakera`.

## Proof and skip reporting

- Capture the user action and the resulting state, not only the final exit code.
- CLI proof includes the command, stdout, stderr, and exit code.
- Mutation proof includes a second view of the vault and the checkout: notes, attachments, `downloads/`, `.cookies/`, and `git status`.
- When the path is an early refusal, record `connect(` and gallery-dl exec counts from `strace -f -e trace=connect,execve`. Kakera has no dry-run flag. A refusal can still be checked; a name is not evidence.
- Record the feature ID and entry point used with every artifact.
- Report an unreachable path with the attempted command and the unmet precondition.
- Do not report a skipped entry point as verified through a different path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behavior.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with tmux` starts with `Preconditions:` and uses labeled bullets that pair each user action with an exact command and observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run.

Keep implementation details out of the map. Name only user paths, stable handles, required state, commands, and observable proof.

## Features

- [Capture a submitted URL](./capture-url.md) covers Obsidian and local capture plus the refusals that write nothing and do not contact a source.
- [Compose submitted URLs](./compose-capture.md) covers one composed Source Note and the refusal when no source produces an image.
- [Add capture tags](./capture-tags.md) covers applying a tag and aborting on an invalid tag before any capture.
- [Process the inbox](./process-inbox.md) covers creating a missing inbox, an empty inbox, and an unchecked task that stays unchecked.
- [Store one image by URL](./single-image-url.md) covers an Obsidian capture of a local image fixture and the same capture with `--share telegram` when the bot token is unset.
