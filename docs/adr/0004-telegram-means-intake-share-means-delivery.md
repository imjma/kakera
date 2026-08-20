# telegram means Intake; share means Delivery

`kakera telegram` is Telegram Intake (read allowed users' private messages and request Transient Telegram Deliveries). The four existing Delivery intents move to `--share telegram`, `--share telegram-only`, `share telegram`, and `share telegram-only`, matching the Capture Tags `share/telegram` and `share/telegram-only`. Keeping Delivery on `telegram` would block the Intake command; `telegram-bot` names the Bot API; a slash in the command word is a shell glob. The slash stays on the tag only.
