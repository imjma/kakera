#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["gallery-dl>=1.32.9,<2"]
# ///

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
CONFIG = Path(os.environ.get("KAKERA_CONFIG", ROOT / "kakera.json"))
COOKIE_DIR = ROOT / ".cookies"
VERSION = "0.1.0"
REDDIT_CLIENT_ID = "6N9uN0krSDE-ig"
TRACKING_PARAMETERS = {
    "igsh",
    "igsi",
    "ref",
    "ref_source",
    "share_id",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}
INBOX_TASK = re.compile(r"^\s*-\s+\[ \]\s+(https?://\S+)")
HTTP_URL = re.compile(r"https?://[^\s<>\[\]\"']+")
INBOX_WATCH_INTERVAL = 2.0
TODOIST_WATCH_INTERVAL = 180.0
TODOIST_API = "https://api.todoist.com/api/v1"
SOURCE_SERVICES = {"instagram", "twitter", "reddit", "rednote"}
TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_RECEIPT = "kakera"
TELEGRAM_TAG = "share/telegram"
TELEGRAM_ONLY_TAG = "share/telegram-only"
INSTAGRAM_SESSION_EXPIRED = "Instagram session expired"
INSTAGRAM_FOLLOWERS_ONLY = "Instagram post is followers-only"
TELEGRAM_STATE = Path(os.environ.get(
    "KAKERA_TELEGRAM_STATE", ROOT / "kakera.telegram-state.json"
))
REPORT_STATE = Path(os.environ.get(
    "KAKERA_REPORT_STATE", TELEGRAM_STATE.with_name("kakera.report-state.json")
))
TELEGRAM_LONG_POLL = 60
TELEGRAM_MAX_IMAGES = 10
TELEGRAM_MAX_BYTES = 10 * 1024 * 1024
TELEGRAM_MAX_VIDEO_BYTES = 50 * 1024 * 1024
IMAGE_FTYP_BRANDS = {b"avif", b"avis", b"heic", b"heix", b"hevc", b"hevx", b"mif1"}


def tag_character_allowed(character: str) -> bool:
    category = unicodedata.category(character)
    if ord(character) < 128:
        return character.isalnum() or character in "_-/"
    return (character.isalnum() or category[0] in {"M", "S"}
            or character == "\u200d" or tag_extender(character))


def tag_extender(character: str) -> bool:
    codepoint = ord(character)
    return (unicodedata.category(character).startswith("M")
            or character == "\u200d"
            or 0x1F3FB <= codepoint <= 0x1F3FF
            or 0xE0020 <= codepoint <= 0xE007F)


def validate_tag_sequence(tag: str) -> None:
    previous = None
    pending_join = False
    emoji_tags = False
    segment_has_base = False
    for character in tag:
        codepoint = ord(character)
        if character == "/":
            if not segment_has_base or pending_join or emoji_tags:
                raise ValueError("invalid tag sequence")
            previous = None
            segment_has_base = False
            continue
        if 0xE0020 <= codepoint <= 0xE007E:
            if not emoji_tags:
                if previous != "\U0001f3f4":
                    raise ValueError("invalid emoji tag sequence")
                emoji_tags = True
            continue
        if codepoint == 0xE007F:
            if not emoji_tags:
                raise ValueError("invalid emoji tag sequence")
            emoji_tags = False
            continue
        if character == "\u200d":
            if pending_join or not previous or unicodedata.category(previous)[0] != "S":
                raise ValueError("invalid emoji joiner")
            pending_join = True
            continue
        if tag_extender(character):
            if not previous or pending_join or (codepoint >= 0x1F3FB and codepoint <= 0x1F3FF
                                                 and unicodedata.category(previous)[0] != "S"):
                raise ValueError("invalid tag extender")
            continue
        if emoji_tags or pending_join and unicodedata.category(character)[0] != "S":
            raise ValueError("invalid emoji sequence")
        previous = character
        segment_has_base = True
        if pending_join:
            pending_join = False
    if not segment_has_base or pending_join or emoji_tags:
        raise ValueError("invalid tag sequence")


def normalize_tag(value: str) -> str:
    """Normalize and validate one Obsidian tag."""
    if not isinstance(value, str):
        raise ValueError("tag must be text")
    tag = unicodedata.normalize("NFKC", value).strip()
    if tag.startswith("#"):
        tag = tag[1:]
    tag = re.sub(r"\s+", "-", tag)
    segments = tag.split("/")
    if (not tag or any(character.isspace() or character == "#" or not tag_character_allowed(character)
                       for character in tag)
            or any(not segment for segment in segments)
            or tag.isnumeric()
            or tag in {".", ".."}):
        raise ValueError(f"invalid Obsidian tag: {value!r}")
    try:
        validate_tag_sequence(tag)
    except ValueError as error:
        raise ValueError(f"invalid Obsidian tag: {value!r}") from error
    return tag


def normalize_tags(values, *, warn: bool = False) -> list[str]:
    tags = []
    for value in values or ():
        try:
            tag = normalize_tag(value)
        except ValueError as error:
            if warn:
                print(f"warning: ignoring {error}", file=sys.stderr)
            else:
                raise
        else:
            if tag.casefold() not in {item.casefold() for item in tags}:
                tags.append(tag)
    return tags


def merge_tags(*groups: list[str]) -> list[str]:
    result = []
    seen = set()
    for group in groups:
        for value in group or ():
            try:
                tag = normalize_tag(value)
            except ValueError:
                continue
            key = tag.casefold()
            if key not in seen:
                seen.add(key)
                result.append(tag)
    return result


def canonical_url(url: str) -> str:
    url = normalize_instagram_post_url(url)
    parts = urlsplit(url)
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parts.query) if key not in TRACKING_PARAMETERS]
    )
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), query, ""))


def normalize_instagram_post_url(url: str) -> str:
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
        return url
    segments = [segment for segment in parts.path.split("/") if segment]
    shape = "reel" if segments and segments[0] == "reels" else (segments[0] if segments else "")
    if len(segments) < 2 or shape not in {"p", "reel", "tv"}:
        return url
    return urlunsplit((parts.scheme, parts.netloc, f"/{shape}/{segments[1]}/", "", ""))


def published_url(url: str) -> str:
    """URL stored on a Source Note and sent in a Telegram caption."""
    url = normalize_instagram_post_url(url)
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    segments = [segment for segment in parts.path.split("/") if segment]
    shape = "reel" if segments and segments[0] == "reels" else (segments[0] if segments else "")
    if host in {"instagram.com", "www.instagram.com", "m.instagram.com"} and (
            len(segments) >= 2 and shape in {"p", "reel", "tv"}):
        return url
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parts.query) if key not in TRACKING_PARAMETERS]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def dedupe_urls(urls: list[str]) -> list[str]:
    seen = set()
    result = []
    for url in urls:
        key = canonical_url(url)
        if key not in seen:
            seen.add(key)
            result.append(url)
    return result


def capture_id(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    rednote_hosts = {"xhslink.com", "www.xhslink.com", "xiaohongshu.com", "www.xiaohongshu.com"}
    if parts.scheme != "https" and not (parts.scheme == "http" and host in rednote_hosts):
        raise ValueError("only HTTPS URLs are supported")

    segments = [segment for segment in parts.path.split("/") if segment]
    post_id = None

    if host in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
        service = "instagram"
        if len(segments) >= 2 and segments[0] in {"p", "reel", "reels", "tv"}:
            post_id = segments[1]
        if not post_id:
            raise ValueError("Instagram URL must be an individual post")
    elif host in {
        "x.com", "www.x.com", "mobile.x.com",
        "twitter.com", "www.twitter.com", "mobile.twitter.com",
    }:
        service = "twitter"
        if len(segments) in {3, 5} and segments[1] == "status":
            post_id = segments[2]
            if len(segments) == 5 and not (
                segments[3] in {"photo", "video"} and segments[4].isdigit()
            ):
                post_id = None
        elif len(segments) in {4, 6} and segments[:3] == ["i", "web", "status"]:
            post_id = segments[3]
            if len(segments) == 6 and not (
                segments[4] in {"photo", "video"} and segments[5].isdigit()
            ):
                post_id = None
        if not post_id or not re.fullmatch(r"\d{1,30}", post_id):
            raise ValueError("Twitter URL must be an individual status")
    elif host in {"reddit.com", "www.reddit.com", "old.reddit.com", "np.reddit.com", "redd.it"}:
        service = "reddit"
        if host == "redd.it" and segments:
            post_id = segments[0]
        else:
            for marker in ("comments", "gallery"):
                if marker in segments and segments.index(marker) + 1 < len(segments):
                    post_id = segments[segments.index(marker) + 1]
                    break
        if not post_id:
            raise ValueError("Reddit URL must be an individual post")
    elif host in rednote_hosts:
        service = "rednote"
        if host in {"xhslink.com", "www.xhslink.com"} and len(segments) >= 2 and segments[0] in {"m", "o"}:
            post_id = segments[1]
        elif host.endswith("xiaohongshu.com"):
            for marker in ("explore", "item"):
                if marker in segments and segments.index(marker) + 1 < len(segments):
                    post_id = segments[segments.index(marker) + 1]
                    break
        if not post_id:
            raise ValueError("RedNote URL must be an individual note")
    else:
        raise ValueError("supported sources are Instagram, Twitter/X, Reddit, and RedNote")

    if not post_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", post_id):
        post_id = hashlib.sha256(canonical_url(url).encode()).hexdigest()[:12]
    return f"{service}-{post_id}"


def read_config() -> dict:
    try:
        config = json.loads(CONFIG.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError, TypeError) as error:
        raise ValueError(f"invalid {CONFIG.name}: {error}") from error
    if not isinstance(config, dict):
        raise ValueError(f"invalid {CONFIG.name}: expected a JSON object")
    return config


def parse_interval(value: str) -> float:
    match = re.fullmatch(r"([0-9]+)([smh])", value.strip(), re.I) if isinstance(value, str) else None
    if not match:
        raise ValueError("must be an integer followed by s, m, or h")
    amount = int(match.group(1))
    if amount < 1:
        raise ValueError("must be at least 1s")
    return float(amount * {"s": 1, "m": 60, "h": 3600}[match.group(2).casefold()])


def configured_interval(section: str, default: float) -> float:
    data = read_config().get(section)
    if not isinstance(data, dict) or "interval" not in data:
        return default
    try:
        return parse_interval(data["interval"])
    except ValueError as error:
        raise ValueError(f"invalid {CONFIG.name}: {section}.interval {error}") from error


def replace_text(temporary: Path, destination: Path) -> None:
    try:
        temporary.replace(destination)
    except OSError as error:
        if error.errno != errno.EPERM:
            raise
        # ponytail: iCloud rejects renames; direct write is the provider fallback.
        destination.write_text(temporary.read_text())
        temporary.unlink()


def configured_browser() -> str | None:
    browser = read_config().get("browser")
    if browser is not None and (not isinstance(browser, str) or not browser.strip()):
        raise ValueError(f"invalid {CONFIG.name}: browser must be a name")
    return browser


def account_name(account: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", account):
        raise ValueError("account must be 1-50 letters, numbers, _ or -")
    return account


def configured_instagram_account() -> str | None:
    instagram = read_config().get("instagram")
    if instagram is None:
        return None
    if not isinstance(instagram, dict):
        raise ValueError(f"invalid {CONFIG.name}: instagram must be an object")
    account = instagram.get("account")
    if account is None:
        return None
    if not isinstance(account, str):
        raise ValueError(f"invalid {CONFIG.name}: instagram.account must be a name")
    return account_name(account)


def configured_twitter_account() -> str | None:
    twitter = read_config().get("twitter")
    if twitter is None:
        return None
    if not isinstance(twitter, dict):
        raise ValueError(f"invalid {CONFIG.name}: twitter must be an object")
    account = twitter.get("account")
    if account is None:
        return None
    if not isinstance(account, str):
        raise ValueError(f"invalid {CONFIG.name}: twitter.account must be a name")
    return account_name(account)


def instagram_cookie_path(account: str) -> Path:
    return COOKIE_DIR / f"instagram-{account_name(account)}.txt"


def twitter_cookie_path(account: str) -> Path:
    return COOKIE_DIR / f"twitter-{account_name(account)}.txt"


def export_cookies(account: str, browser: str, service: str) -> int:
    if service == "instagram":
        destination = instagram_cookie_path(account)
        domains = ("instagram.com",)
        required = ("sessionid",)
        label = "Instagram"
    else:
        destination = twitter_cookie_path(account)
        domains = ("x.com", "twitter.com")
        required = ("auth_token", "ct0")
        label = "Twitter"
    with tempfile.TemporaryDirectory(prefix="kakera-cookies-") as directory:
        exported = Path(directory) / "cookies.txt"
        command = [
            sys.executable, "-m", "gallery_dl", "--config-ignore", "--no-input",
            "--cookies-from-browser", browser, "--cookies-export", str(exported), "noop",
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            error = next(
                (line for line in reversed(result.stderr.splitlines()) if line.strip()),
                "cookie export failed",
            )
            print(f"error: {error}", file=sys.stderr)
            return result.returncode
        try:
            lines = exported.read_text().splitlines()
        except OSError as error:
            print(f"error: cannot read exported cookies: {error}", file=sys.stderr)
            return 1

    kept = []
    authenticated = set()
    for line in lines:
        fields = line.removeprefix("#HttpOnly_").split("\t")
        if len(fields) != 7:
            continue
        domain = fields[0].lstrip(".")
        if not any(domain == base or domain.endswith(f".{base}") for base in domains):
            continue
        kept.append(line)
        if fields[5] in required and fields[6]:
            authenticated.add(fields[5])
    if not all(cookie in authenticated for cookie in required):
        print(f"error: {browser} has no logged-in {label} session", file=sys.stderr)
        return 1

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.chmod(0o700)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text("# Netscape HTTP Cookie File\n\n" + "\n".join(kept) + "\n")
    temporary.chmod(0o600)
    temporary.replace(destination)
    destination.chmod(0o600)
    print(f"ok: saved {label} account {account!r} from {browser}")
    return 0


def instagram_cookies(account: str, browser: str = "orion") -> int:
    return export_cookies(account, browser, "instagram")


def twitter_cookies(account: str, browser: str = "orion") -> int:
    return export_cookies(account, browser, "twitter")


def configured_reddit() -> tuple[str, str] | None:
    reddit = read_config().get("reddit")
    if reddit is None:
        return None
    if not isinstance(reddit, dict):
        raise ValueError(f"invalid {CONFIG.name}: reddit must be an object")
    client_id, user_agent = reddit.get("client_id"), reddit.get("user_agent")
    if not all(isinstance(value, str) and value.strip() for value in (client_id, user_agent)):
        raise ValueError(f"invalid {CONFIG.name}: reddit needs client_id and user_agent")
    return client_id, user_agent


def reddit_oauth(username: str) -> int:
    username = username.strip().removeprefix("/u/").removeprefix("u/")
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,20}", username):
        raise ValueError("Reddit username must be 3-20 letters, numbers, _ or -")
    result = subprocess.run([
        sys.executable, "-m", "gallery_dl", "--config-ignore", "oauth:reddit",
    ])
    if result.returncode:
        return result.returncode
    config = read_config()
    config["reddit"] = {
        "client_id": REDDIT_CLIENT_ID,
        "user_agent": f"Python:kakera:v{VERSION} (by /u/{username})",
    }
    temporary = CONFIG.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    replace_text(temporary, CONFIG)
    print(f"ok: updated {CONFIG}")
    return 0


def configured_paths(*names: str) -> tuple[Path, ...]:
    try:
        config = read_config()["obsidian"]
        vault = Path(config["vault"]).expanduser()
        paths = tuple(Path(config[name]) for name in names)
    except (KeyError, TypeError) as error:
        raise ValueError(f"invalid {CONFIG.name}: {error}") from error
    if not vault.is_dir():
        raise ValueError(f"Obsidian vault does not exist: {vault}")
    if any(path.is_absolute() or ".." in path.parts for path in paths):
        raise ValueError("Obsidian paths must stay inside the vault")
    try:
        resolved_vault = vault.resolve()
        if any(not (vault / path).resolve().is_relative_to(resolved_vault) for path in paths):
            raise ValueError("Obsidian paths must stay inside the vault")
    except OSError as error:
        raise ValueError(f"cannot resolve Obsidian paths: {error}") from error
    return tuple(vault / path for path in paths)


def configured_folders() -> tuple[Path, Path]:
    return configured_paths("notes", "attachments")


def configured_inbox() -> Path:
    return configured_paths("inbox")[0]


def configured_todoist_project() -> str:
    todoist = read_config().get("todoist")
    if not isinstance(todoist, dict):
        raise ValueError(f"invalid {CONFIG.name}: todoist must be an object")
    project_id = todoist.get("project_id")
    if not isinstance(project_id, (str, int)) or not str(project_id).strip():
        raise ValueError(f"invalid {CONFIG.name}: todoist.project_id must be set")
    return str(project_id)


def todoist_token() -> str:
    token = os.environ.get("TODOIST_API_TOKEN", "").strip()
    if not token:
        raise ValueError("TODOIST_API_TOKEN is not set")
    return token


def configured_telegram_chat_id() -> str:
    telegram = read_config().get("telegram")
    if not isinstance(telegram, dict):
        raise ValueError(f"invalid {CONFIG.name}: telegram must be an object")
    try:
        return canonical_telegram_chat_id(telegram.get("chat_id"))
    except ValueError as error:
        raise ValueError(f"invalid {CONFIG.name}: telegram.chat_id must be numeric") from error


def canonical_telegram_chat_id(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("chat ID must be numeric")
    text = str(value).strip()
    if not re.fullmatch(r"-?\d+", text):
        raise ValueError("chat ID must be numeric")
    return str(int(text))


def telegram_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")
    return token


def canonical_telegram_user_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("user ID must be numeric")
    text = str(value).strip()
    if not re.fullmatch(r"\d+", text):
        raise ValueError("user ID must be numeric")
    user_id = int(text)
    if user_id <= 0:
        raise ValueError("user ID must be numeric")
    return user_id


def configured_telegram_allowed_user_ids() -> list[int]:
    telegram = read_config().get("telegram")
    if not isinstance(telegram, dict):
        raise ValueError(f"invalid {CONFIG.name}: telegram must be an object")
    values = telegram.get("allowed_user_ids")
    if not isinstance(values, list) or not values:
        raise ValueError(
            f"invalid {CONFIG.name}: telegram.allowed_user_ids must be a non-empty array of numeric user IDs"
        )
    ids = []
    seen = set()
    for value in values:
        try:
            user_id = canonical_telegram_user_id(value)
        except ValueError as error:
            raise ValueError(
                f"invalid {CONFIG.name}: telegram.allowed_user_ids must be numeric user IDs"
            ) from error
        if user_id not in seen:
            seen.add(user_id)
            ids.append(user_id)
    return ids


def configured_telegram_report_user_id() -> int | None:
    telegram = read_config().get("telegram")
    if not isinstance(telegram, dict) or telegram.get("report_user_id") is None:
        return None
    try:
        user_id = canonical_telegram_user_id(telegram.get("report_user_id"))
    except ValueError as error:
        raise ValueError(f"invalid {CONFIG.name}: telegram.report_user_id must be numeric") from error
    if user_id not in configured_telegram_allowed_user_ids():
        raise ValueError(
            f"invalid {CONFIG.name}: telegram.report_user_id must be listed in telegram.allowed_user_ids"
        )
    return user_id


def source_service_urls(text: str) -> list[str]:
    found = []
    for match in HTTP_URL.finditer(text):
        url = match.group(0).rstrip(".,!?;:)]}")
        try:
            capture_id(url)
        except ValueError:
            continue
        found.append(url)
    return dedupe_urls(found)


def _telegram_timeout_error(error: BaseException) -> bool:
    if isinstance(error, TimeoutError):
        return True
    reason = getattr(error, "reason", None)
    return isinstance(reason, TimeoutError) or "timed out" in str(reason or error).casefold()


def _telegram_api_error(method: str, body: object = None, error: BaseException | None = None) -> ValueError:
    description = body.get("description") if isinstance(body, dict) else None
    code = body.get("error_code") if isinstance(body, dict) else None
    http_code = getattr(error, "code", None)
    if isinstance(description, str) and description.strip():
        text = description.strip()
        if "webhook" in text.casefold():
            return ValueError("Telegram webhook is set; delete it before kakera telegram")
        if (code == 409 or http_code == 409 or "terminated by other getUpdates" in text.casefold()
                or (method == "getUpdates" and "conflict" in text.casefold())):
            return ValueError("Telegram getUpdates conflict; another consumer is using this bot")
        return ValueError(f"Telegram request failed: {text}")
    if method == "getUpdates" and http_code == 409:
        return ValueError("Telegram getUpdates conflict; another consumer is using this bot")
    if error is not None and _telegram_timeout_error(error):
        return ValueError("Telegram request timed out")
    return ValueError("Telegram request failed")


def _telegram_delivery_message(error: BaseException) -> str:
    text = str(error)
    if text.startswith("Telegram request failed: ") or "webhook" in text.casefold():
        return text
    return "Telegram delivery uncertain; check the configured chat before retrying"


def telegram_json(method: str, token: str, payload: dict | None = None, *, timeout: int = 30):
    request = Request(
        f"{TELEGRAM_API}/bot{token}/{method}",
        data=json.dumps({} if payload is None else payload).encode(),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            if getattr(response, "status", 200) >= 400:
                raise OSError("Telegram HTTP error")
            body = json.loads(response.read() or b"{}")
    except HTTPError as error:
        try:
            body = json.loads(error.read() or b"{}")
        except (OSError, json.JSONDecodeError, TypeError):
            body = {}
        raise _telegram_api_error(method, body, error) from error
    except (OSError, json.JSONDecodeError, TypeError) as error:
        raise _telegram_api_error(method, error=error) from error
    if not isinstance(body, dict) or body.get("ok") is not True:
        raise _telegram_api_error(method, body)
    return body.get("result")


def telegram_webhook_url(token: str) -> str:
    result = telegram_json("getWebhookInfo", token)
    if not isinstance(result, dict):
        raise ValueError("Telegram request failed")
    url = result.get("url")
    if url in (None, ""):
        return ""
    if not isinstance(url, str):
        raise ValueError("Telegram request failed")
    return url


def telegram_get_updates(token: str, offset: int | None, timeout: int) -> list[dict]:
    payload = {"timeout": timeout, "allowed_updates": ["message"]}
    if offset is not None:
        payload["offset"] = offset
    result = telegram_json("getUpdates", token, payload, timeout=timeout + 10)
    if not isinstance(result, list):
        raise ValueError("Telegram request failed")
    updates = []
    for item in result:
        update_id = item.get("update_id") if isinstance(item, dict) else None
        if isinstance(update_id, bool) or not isinstance(update_id, int):
            raise ValueError("Telegram returned an invalid update")
        updates.append(item)
    return updates


def telegram_send_text(chat_id: str, text: str, token: str) -> None:
    telegram_json("sendMessage", token, {"chat_id": chat_id, "text": text[:4096]})


def instagram_named_error(text: str | None) -> str | None:
    if not isinstance(text, str):
        return None
    if INSTAGRAM_FOLLOWERS_ONLY in text:
        return INSTAGRAM_FOLLOWERS_ONLY
    if INSTAGRAM_SESSION_EXPIRED in text:
        return INSTAGRAM_SESSION_EXPIRED
    return None


def instagram_fetch_error(stderr: str) -> str | None:
    folded = stderr.casefold()
    if "posts are private" in folded or "post is private" in folded:
        return INSTAGRAM_FOLLOWERS_ONLY
    if "[instagram]" in folded and "login page" in folded:
        return INSTAGRAM_SESSION_EXPIRED
    return None


def queue_failure_seen(reported: dict | None, urls: list[str], message: str) -> bool:
    if reported is None:
        return False
    reason = instagram_named_error(message)
    if reason:
        already = reported.get(("instagram", reason))
        return isinstance(already, set) and all(
            published_url(url) in already for url in urls
        )
    detail = f"{', '.join(published_url(url) for url in urls)}: {message}"
    return reported.get(("capture", tuple(urls))) == detail


def log_queue_result(reported: dict | None, urls: list[str], success: bool, message: str) -> None:
    if success or not queue_failure_seen(reported, urls, message):
        print(f"{process_stamp()} {'ok' if success else 'error'}: {', '.join(urls)}: {message}")


def load_report_state() -> dict:
    try:
        data = json.loads(REPORT_STATE.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    reported: dict = {}
    instagram = data.get("instagram")
    if isinstance(instagram, dict):
        for reason in (INSTAGRAM_SESSION_EXPIRED, INSTAGRAM_FOLLOWERS_ONLY):
            urls = instagram.get(reason)
            if isinstance(urls, list):
                reported[("instagram", reason)] = {
                    url for url in urls if isinstance(url, str)
                }
    for item in data.get("capture") or []:
        if not isinstance(item, dict):
            continue
        urls, detail = item.get("urls"), item.get("detail")
        if isinstance(urls, list) and all(isinstance(url, str) for url in urls) and isinstance(detail, str):
            reported[("capture", tuple(urls))] = detail
    watch = data.get("watch")
    if isinstance(watch, str):
        reported["watch"] = watch
    return reported


def save_report_state(reported: dict) -> None:
    instagram = {}
    capture = []
    watch = reported.get("watch")
    for key, value in reported.items():
        if isinstance(key, tuple) and len(key) == 2 and key[0] == "instagram" and isinstance(value, set):
            instagram[key[1]] = sorted(value)
        elif isinstance(key, tuple) and len(key) == 2 and key[0] == "capture" and isinstance(value, str):
            capture.append({"urls": list(key[1]), "detail": value})
    payload = {"instagram": instagram, "capture": capture}
    if isinstance(watch, str):
        payload["watch"] = watch
    try:
        REPORT_STATE.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, separators=(",", ":")) + "\n"
        temporary = REPORT_STATE.with_suffix(REPORT_STATE.suffix + ".tmp")
        temporary.write_text(encoded)
        os.replace(temporary, REPORT_STATE)
    except OSError:
        return


def format_named_instagram(named: dict[str, list[str]]) -> str:
    parts = []
    for reason in (INSTAGRAM_SESSION_EXPIRED, INSTAGRAM_FOLLOWERS_ONLY):
        urls = named.get(reason)
        if urls:
            parts.append(f"{reason}: {', '.join(published_url(url) for url in urls)}")
    return "; ".join(parts)


def report_named_instagram_failures(
    origin: str,
    named: dict[str, list[str]],
    reported: dict | None,
) -> None:
    for reason in (INSTAGRAM_SESSION_EXPIRED, INSTAGRAM_FOLLOWERS_ONLY):
        urls = list(dict.fromkeys(published_url(url) for url in named.get(reason, [])))
        key = ("instagram", reason)
        already = reported.get(key) if reported is not None else None
        if not isinstance(already, set):
            already = set()
        current = set(urls)
        if reported is not None:
            already &= current
            reported[key] = already
        new = [url for url in urls if url not in already]
        if not new:
            continue
        if report_queue_failure(origin, reason + "\n" + "\n".join(new)):
            already.update(new)
            if reported is not None:
                reported[key] = already


def report_queue_failure(
    origin: str,
    detail: str,
    *,
    key: object = None,
    reported: dict | None = None,
) -> bool:
    if reported is not None and key is not None and reported.get(key) == detail:
        return False
    try:
        token = telegram_token()
        user_id = configured_telegram_report_user_id()
    except ValueError as error:
        if str(error) != "TELEGRAM_BOT_TOKEN is not set":
            stamp_print(f"error: {error}", error=True)
        return False
    if user_id is None:
        return False
    try:
        telegram_send_text(str(user_id), f"{origin}: {detail}", token)
    except ValueError as error:
        stamp_print(f"error: cannot report in Telegram: {error}", error=True)
        return False
    if reported is not None and key is not None:
        reported[key] = detail
    return True


@contextmanager
def telegram_intake_state():
    fd = os.open(TELEGRAM_STATE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("telegram intake already running") from None
        try:
            yield fd
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def telegram_intake_offset(fd: int) -> int | None:
    os.lseek(fd, 0, os.SEEK_SET)
    data = os.read(fd, 4096)
    if not data.strip():
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid {TELEGRAM_STATE.name}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"invalid {TELEGRAM_STATE.name}")
    value = payload.get("update_id")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid {TELEGRAM_STATE.name}: update_id must be a non-negative integer")
    return value


def set_telegram_intake_offset(fd: int, update_id: int) -> None:
    encoded = (json.dumps({"update_id": update_id}, separators=(",", ":")) + "\n").encode()
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    os.write(fd, encoded)
    os.fsync(fd)


def intake_message_urls(message: dict) -> list[str]:
    chunks = []
    for key in ("text", "caption"):
        value = message.get(key)
        if isinstance(value, str):
            chunks.append(value)
    return source_service_urls("\n".join(chunks))


def intake_private_message(update: dict, allowed: set[int]) -> tuple[dict, str] | tuple[None, None]:
    message = update.get("message")
    if not isinstance(message, dict):
        return None, None
    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, dict) or not isinstance(sender, dict):
        return None, None
    if chat.get("type") != "private" or sender.get("is_bot") is True:
        return None, None
    try:
        user_id = canonical_telegram_user_id(sender.get("id"))
        chat_id = canonical_telegram_chat_id(chat.get("id"))
    except ValueError:
        return None, None
    if user_id not in allowed:
        return None, None
    return message, chat_id


def process_intake_update(
    update: dict,
    allowed: set[int],
    token: str,
    browser: str | None,
    account: str | None,
    twitter_account: str | None,
) -> bool:
    message, chat_id = intake_private_message(update, allowed)
    if message is None:
        return False
    urls = intake_message_urls(message)
    if not urls:
        return False
    failed_lines = []
    failed = False
    for url in urls:
        try:
            success, text = telegram_only_url(
                url, browser, account, twitter_account,
                sender=telegram_sender_name(message.get("from") or {}),
            )
        except (OSError, ValueError) as error:
            success, text = False, f"Telegram not sent: {error}"
        shown = published_url(url)
        print(f"{process_stamp()} {'ok' if success else 'error'}: {shown}: {text}")
        if not success:
            failed = True
            failed_lines.append(f"{shown}: {text}")
    if failed_lines:
        try:
            telegram_send_text(chat_id, "\n".join(failed_lines), token)
        except ValueError as error:
            stamp_print(f"error: cannot reply in Telegram: {error}", error=True)
    return failed


def consume_telegram_updates(
    fd: int,
    token: str,
    browser: str | None,
    account: str | None,
    twitter_account: str | None,
    timeout: int,
) -> tuple[int, bool]:
    allowed = set(configured_telegram_allowed_user_ids())
    last = telegram_intake_offset(fd)
    offset = last + 1 if last is not None else None
    updates = telegram_get_updates(token, offset, timeout)
    failed = False
    for update in updates:
        failed |= process_intake_update(update, allowed, token, browser, account, twitter_account)
        set_telegram_intake_offset(fd, update["update_id"])
    return len(updates), failed


def telegram_intake(
    browser: str | None,
    account: str | None,
    twitter_account: str | None,
    *,
    watch: bool,
) -> int:
    token = telegram_token()
    configured_telegram_chat_id()
    configured_telegram_allowed_user_ids()
    if telegram_webhook_url(token):
        raise ValueError("Telegram webhook is set; delete it before kakera telegram")
    with telegram_intake_state() as fd:
        if watch:
            stamp_print("ok: watching Telegram Intake")
            reported_error = None
            try:
                while True:
                    try:
                        consume_telegram_updates(
                            fd, token, browser, account, twitter_account, TELEGRAM_LONG_POLL
                        )
                        reported_error = None
                    except (OSError, ValueError) as error:
                        if str(error) != reported_error:
                            stamp_print(f"error: {error}", error=True)
                            reported_error = str(error)
                        time.sleep(1)
            except KeyboardInterrupt:
                stamp_print("ok: stopped watching Telegram Intake")
                return 0
        failed = False
        while True:
            count, batch_failed = consume_telegram_updates(
                fd, token, browser, account, twitter_account, 0
            )
            failed |= batch_failed
            if count == 0:
                break
        return int(failed)


def todoist_request(token: str, path: str, method: str = "GET") -> dict | None:
    request = Request(
        f"{TODOIST_API}{path}",
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
        method=method,
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read()
    except OSError as error:
        raise ValueError(f"Todoist request failed: {error}") from error
    if not body:
        return {}
    try:
        result = json.loads(body)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("Todoist returned invalid JSON") from error
    if result is None and method == "POST":
        return None
    if not isinstance(result, dict):
        raise ValueError("Todoist returned an invalid response")
    return result


def todoist_tasks(token: str, project_id: str):
    cursor = None
    while True:
        query = {"project_id": project_id}
        if cursor:
            query["cursor"] = cursor
        result = todoist_request(token, f"/tasks?{urlencode(query)}")
        tasks = result.get("results")
        if not isinstance(tasks, list):
            raise ValueError("Todoist returned an invalid task list")
        yield from tasks
        cursor = result.get("next_cursor")
        if not cursor:
            return


def todoist_task_pages(token: str, project_id: str):
    cursor = None
    while True:
        query = {"project_id": project_id}
        if cursor:
            query["cursor"] = cursor
        result = todoist_request(token, f"/tasks?{urlencode(query)}")
        tasks = result.get("results")
        if not isinstance(tasks, list):
            raise ValueError("Todoist returned an invalid task list")
        yield [task for task in tasks if isinstance(task, dict)]
        cursor = result.get("next_cursor")
        if not cursor:
            return


def task_urls(task: dict) -> list[str]:
    urls = []
    for field in ("content", "description"):
        value = task.get(field)
        if isinstance(value, str):
            urls.extend(match.group(0).rstrip(".,!?;:)]}") for match in HTTP_URL.finditer(value))
    return urls


def task_tags(task: dict) -> list[str]:
    """Return native Todoist labels, preserving the API order."""
    labels = task.get("labels", [])
    return [label for label in labels if isinstance(label, str)] if isinstance(labels, list) else []


def queue_tags(values) -> list[str]:
    return normalize_tags(values, warn=True)


def inbox_line_tags(text: str) -> list[str]:
    # Remove complete URLs first so #fragments are never mistaken for tags.
    text = HTTP_URL.sub("", text)
    tags = []
    index = 0
    while index < len(text):
        if text[index] != "#" or (index and (
            text[index - 1].isalnum() or text[index - 1] in "_#"
        )):
            index += 1
            continue
        index += 1
        characters = []
        while index < len(text) and tag_character_allowed(text[index]):
            characters.append(text[index])
            index += 1
        if characters:
            tags.append("".join(characters))
    return tags


def task_url(task: dict) -> str | None:
    return next(iter(task_urls(task)), None)


def process_stamp() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def stamp_print(message: str, *, error: bool = False) -> None:
    print(f"{process_stamp()} {message}", file=sys.stderr if error else sys.stdout)


def process_todoist(
    browser: str | None,
    notes: Path,
    attachments: Path,
    account: str | None = None,
    twitter_account: str | None = None,
    tags: list[str] | None = None,
    reported: dict | None = None,
) -> int:
    token = todoist_token()
    project_id = configured_todoist_project()
    failed = False
    named: dict[str, list[str]] = {}
    pages = todoist_task_pages(token, project_id)
    first_page = next(pages, [])
    hierarchy = any("parent_id" in task for task in first_page)
    tasks = first_page
    if hierarchy:
        for page in pages:
            tasks.extend(page)
    by_parent: dict[str | None, list[dict]] = {}
    for task in tasks:
        by_parent.setdefault(str(task.get("parent_id")) if task.get("parent_id") else None, []).append(task)
    for children in by_parent.values():
        children.sort(key=lambda task: (task.get("child_order", 0), str(task.get("id", ""))))
    roots = by_parent.get(None, [])

    def tree_urls(task: dict):
        yield from task_urls(task)
        for child in by_parent.get(str(task.get("id")), []):
            yield from tree_urls(child)

    def tree_tags(task: dict):
        yield from task_tags(task)
        for child in by_parent.get(str(task.get("id")), []):
            yield from tree_tags(child)

    if hierarchy:
        work = iter(roots)
    else:
        def sequential_tasks():
            yield from first_page
            for page in pages:
                yield from page
        work = sequential_tasks()
    for task in work:
        if not isinstance(task, dict):
            raise ValueError("Todoist returned an invalid task")
        urls = dedupe_urls(list(tree_urls(task))) if hierarchy else dedupe_urls(task_urls(task))
        if not urls:
            print(f"{process_stamp()} ok: skipped Todoist task {task.get('id', '?')}: no HTTP(S) URL")
            continue
        item_tags = queue_tags(list(tree_tags(task))) if hierarchy else queue_tags(task_tags(task))
        capture_tags = merge_tags(item_tags, tags or [])
        if telegram_only_requested(capture_tags):
            if not process_transient_urls(
                urls, browser, account, twitter_account,
                origin="Todoist", reported=reported, named_failures=named,
            ):
                failed = True
                continue
            try:
                todoist_request(token, f"/tasks/{quote(str(task.get('id', '')), safe='')}/close", "POST")
            except ValueError as error:
                print(f"{process_stamp()} error: Todoist task {task.get('id', '?')} was sent but not closed: {error}", file=sys.stderr)
                failed = True
                report_queue_failure(
                    "Todoist",
                    f"task {task.get('id', '?')} was sent but not closed: {error}",
                    key=("close", str(task.get("id", ""))),
                    reported=reported,
                )
                continue
            if reported is not None:
                reported.pop(("close", str(task.get("id", ""))), None)
            continue
        success, message = save_composed(
            urls, browser, notes, attachments, account, twitter_account,
            capture_tags or None, named_failures=named,
        )
        log_queue_result(reported, urls, success, message)
        capture_key = ("capture", tuple(urls))
        if not success:
            failed = True
            if instagram_named_error(message) is None or "no sources produced" in message:
                report_queue_failure(
                    "Todoist",
                    f"{', '.join(published_url(url) for url in urls)}: {message}",
                    key=capture_key,
                    reported=reported,
                )
            continue
        if reported is not None:
            reported.pop(capture_key, None)
        try:
            todoist_request(token, f"/tasks/{quote(str(task.get('id', '')), safe='')}/close", "POST")
        except ValueError as error:
            print(f"{process_stamp()} error: Todoist task {task.get('id', '?')} was saved but not closed: {error}", file=sys.stderr)
            failed = True
            report_queue_failure(
                "Todoist",
                f"task {task.get('id', '?')} was saved but not closed: {error}",
                key=("close", str(task.get("id", ""))),
                reported=reported,
            )
            continue
        if reported is not None:
            reported.pop(("close", str(task.get("id", ""))), None)
    report_named_instagram_failures("Todoist", named, reported)
    return int(failed)


def watch_todoist(
    browser: str | None,
    notes: Path,
    attachments: Path,
    account: str | None = None,
    interval: float = TODOIST_WATCH_INTERVAL,
    twitter_account: str | None = None,
    tags: list[str] | None = None,
) -> int:
    stamp_print("ok: watching Todoist")
    reported = load_report_state()
    try:
        while True:
            try:
                process_todoist(
                    browser, notes, attachments, account, twitter_account, tags,
                    reported=reported,
                )
            except (OSError, ValueError) as error:
                if str(error) != reported.get("watch"):
                    stamp_print(f"error: {error}", error=True)
                report_queue_failure("Todoist", str(error), key="watch", reported=reported)
            else:
                reported.pop("watch", None)
            save_report_state(reported)
            time.sleep(interval)
    except KeyboardInterrupt:
        stamp_print("ok: stopped watching Todoist")
        return 0


def image_extension(path: Path) -> str | None:
    with path.open("rb") as file:
        data = file.read(16)
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data[:4] in {b"II*\x00", b"MM\x00*"}:
        return ".tiff"
    if data[4:8] == b"ftyp" and data[8:12] in IMAGE_FTYP_BRANDS:
        return ".avif" if data[8:12] in {b"avif", b"avis"} else ".heic"
    return None


def video_extension(path: Path) -> str | None:
    with path.open("rb") as file:
        data = file.read(16)
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] not in IMAGE_FTYP_BRANDS:
        return ".mp4"
    return None


def media_extension(path: Path, *, allow_video: bool = False) -> str | None:
    return image_extension(path) or (video_extension(path) if allow_video else None)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


@contextmanager
def capture_lock(attachment_root: Path, service: str, name: str):
    del service, name
    runtime_root = Path(tempfile.gettempdir()) / f"kakera-locks-{os.getuid()}"
    try:
        os.mkdir(runtime_root, 0o700)
    except FileExistsError:
        pass
    root_stat = os.lstat(runtime_root)
    if not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_uid != os.getuid() or root_stat.st_mode & 0o077:
        raise OSError(f"unsafe capture lock directory: {runtime_root}")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    dir_fd = os.open(runtime_root, directory_flags)
    lock_fd = None
    acquired = False
    try:
        opened_stat = os.fstat(dir_fd)
        if (not stat.S_ISDIR(opened_stat.st_mode) or opened_stat.st_uid != os.getuid()
                or opened_stat.st_mode & 0o077):
            raise OSError(f"unsafe capture lock directory: {runtime_root}")
        # ponytail: one lock per canonical output root; split per capture only if throughput matters.
        identity = canonical_lock_path(attachment_root).encode()
        lock_name = f"{hashlib.sha256(identity).hexdigest()}.lock"
        try:
            existing = os.stat(lock_name, dir_fd=dir_fd, follow_symlinks=False)
            if not stat.S_ISREG(existing.st_mode):
                raise OSError(f"unsafe capture lock file: {lock_name}")
        except FileNotFoundError:
            pass
        file_flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        for attempt in range(3):
            try:
                lock_fd = os.open(lock_name, file_flags, 0o600, dir_fd=dir_fd)
                break
            except FileNotFoundError:
                if attempt == 2:
                    raise
                time.sleep(0)
        lock_stat = os.fstat(lock_fd)
        if (not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_uid != os.getuid()
                or lock_stat.st_mode & 0o077 or lock_stat.st_nlink != 1):
            raise OSError(f"unsafe capture lock file: {lock_name}")
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise OSError("capture lock timed out")
                time.sleep(0.05)
        try:
            yield
        finally:
            if acquired:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        os.close(dir_fd)


def canonical_lock_path(path: Path) -> str:
    """Return a stable, case/unicode-insensitive identity for a filesystem root."""
    resolved = Path(path).expanduser().resolve(strict=False)
    return "/".join(unicodedata.normalize("NFD", part).casefold() for part in resolved.parts)


def _rednote_https(url: object) -> str | None:
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return url.replace("http://", "https://", 1)
    return None


def _rednote_video_url(note: dict) -> str | None:
    video = note.get("video")
    if not isinstance(video, dict):
        return None
    media = video.get("media")
    stream = media.get("stream") if isinstance(media, dict) else None
    if isinstance(stream, dict):
        for codec in ("h264", "h265", "h266", "hevc", "av1"):
            items = stream.get(codec)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                for key in ("masterUrl", "url"):
                    found = _rednote_https(item.get(key))
                    if found:
                        return found
                backups = item.get("backupUrls")
                if isinstance(backups, list):
                    for backup in backups:
                        found = _rednote_https(backup)
                        if found:
                            return found
    found = _rednote_https(video.get("url"))
    if found:
        return found
    consumer = media.get("consumer") if isinstance(media, dict) else None
    key = None
    if isinstance(consumer, dict):
        key = consumer.get("originVideoKey")
    if not key and isinstance(media, dict):
        key = media.get("originVideoKey")
    if isinstance(key, str) and key and not key.startswith(("http://", "https://")):
        return f"https://sns-video-bd.xhscdn.com/{key.lstrip('/')}"
    return _rednote_https(key)


def parse_rednote(page: str, source: str) -> tuple[str, dict, list[str], str | None]:
    match = re.search(r"window\.__INITIAL_STATE__=(.*?)</script>", page, re.S)
    if not match:
        raise ValueError("RedNote page did not include public note data")
    try:
        state = json.loads(re.sub(r"(?<=:)undefined(?=[,}])", "null", match.group(1)))
        note = next(iter(state["note"]["noteDetailMap"].values()))["note"]
        if not isinstance(note, dict):
            raise ValueError("RedNote page data has changed")
        note_id = note.get("noteId")
    except (KeyError, StopIteration, TypeError, json.JSONDecodeError) as error:
        raise ValueError("RedNote page data has changed") from error

    images = [
        image.get("urlDefault") or next(
            (item.get("url") for item in image.get("infoList", []) if item.get("url")), ""
        )
        for image in note.get("imageList", [])
    ]
    images = [url.replace("http://", "https://", 1) for url in images if url]
    video = _rednote_video_url(note)
    if not images and not video:
        raise ValueError("RedNote note has no public images")

    user = note.get("user") or {}
    timestamp = note.get("time")
    metadata = {
        "title": note.get("title"),
        "description": note.get("desc"),
        "author": user.get("nickname"),
        "author_url": (
            f"https://www.xiaohongshu.com/user/profile/{user['userId']}"
            if user.get("userId") else None
        ),
        "published": (
            datetime.fromtimestamp(timestamp / 1000).astimezone().isoformat(timespec="seconds")
            if isinstance(timestamp, (int, float)) else None
        ),
        "post_url": source,
    }
    if not isinstance(note_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", note_id):
        note_id = hashlib.sha256(canonical_url(source).encode()).hexdigest()[:12]
    return f"rednote-{note_id}", metadata, images, video


def rednote_fetch_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme.lower() == "http":
        return urlunsplit(("https", parts.netloc, parts.path, parts.query, parts.fragment))
    return url


def _rednote_fetch_bytes(url: str, headers: dict, limit: int, label: str) -> bytes:
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"RedNote {label} exceeds 50 MB")
    return data


def download_rednote(url: str, directory: Path, include_video: bool = False) -> tuple[str, dict]:
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) Kakera/0.1"}
    try:
        with urlopen(Request(rednote_fetch_url(url), headers=headers), timeout=30) as response:
            page = response.read(5 * 1024 * 1024 + 1)
            final_url = response.url
        if len(page) > 5 * 1024 * 1024:
            raise ValueError("RedNote page is unexpectedly large")
        name, metadata, images, video = parse_rednote(page.decode("utf-8"), url)
        if not images and not (include_video and video):
            raise ValueError("RedNote note has no public images")
        file_headers = {**headers, "Referer": final_url}
        for index, image_url in enumerate(images, 1):
            data = _rednote_fetch_bytes(image_url, file_headers, 50 * 1024 * 1024, "image")
            (directory / f"{index:02}.image").write_bytes(data)
        if include_video and video:
            data = _rednote_fetch_bytes(video, file_headers, 50 * 1024 * 1024, "video")
            (directory / f"{len(images) + 1:02}.video").write_bytes(data)
        return name, metadata
    except OSError as error:
        raise ValueError(f"RedNote request failed: {error}") from error


def post_title(metadata: dict, service: str) -> str:
    if service == "twitter":
        description = metadata.get("content") if isinstance(metadata.get("content"), str) else ""
    else:
        description = metadata.get("description") or metadata.get("content") or metadata.get("selftext") or ""
    title = None if service == "twitter" else metadata.get("title")
    return title or next(
        (line.strip() for line in description.splitlines() if line.strip()),
        (metadata.get("_capture_post_id") if metadata.get("_capture_post_id") else None)
        or f"{service.title()} capture",
    )


def note_title(metadata: dict, service: str) -> str:
    service_name = "小红书" if service == "rednote" else service.title()
    return f"{post_title(metadata, service)} - {service_name}"


def safe_filename(title: str) -> str:
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", title).strip(" .")
    return title


def note_filename(metadata: dict, service: str, post_id: str | None = None) -> str:
    service_name = "小红书" if service == "rednote" else service.title()
    suffix = f" - {post_id} - {service_name}" if post_id else f" - {service_name}"
    title = safe_filename(post_title(metadata, service))
    budget = 240 - len(suffix.encode("utf-8"))
    title = title.encode("utf-8")[:budget].decode("utf-8", "ignore").rstrip(" .")
    return f"{title}{suffix}"


def frontmatter_body(text: str) -> str | None:
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return None
    closing = next((index for index, line in enumerate(lines[1:], 1)
                    if line.rstrip() == "---"), None)
    return None if closing is None else "\n".join(lines[1:closing])


def note_path(notes: Path, name: str, metadata: dict, source: str | None = None) -> Path:
    service, post_id = name.split("-", 1)
    source_url = source or metadata.get("post_url")
    property_key = "xhslink" if service == "rednote" else service
    if service not in {"instagram", "twitter", "reddit", "rednote"}:
        property_key = "source"
    for path in notes.glob("*.md"):
        try:
            text = path.read_text()
            if source_url:
                frontmatter = frontmatter_body(text)
                if frontmatter is not None:
                    for line in frontmatter.splitlines():
                        key, separator, raw_value = line.partition(":")
                        if separator and key.strip() == property_key:
                            try:
                                value = json.loads(raw_value.strip())
                            except json.JSONDecodeError:
                                value = ""
                            if isinstance(value, str) and canonical_url(value) == canonical_url(source_url):
                                return path
                            break
            if f"{name}-" in text:
                return path
        except OSError:
            pass
    filename = note_filename(metadata, service)
    candidate = notes / f"{filename or name}.md"
    if candidate.exists():
        filename = note_filename(metadata, service, post_id)
        candidate = notes / f"{filename or name}.md"
    return candidate


def _tag_value(raw: str) -> str | None:
    raw = raw.strip()
    if not raw:
        return None
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1].replace("''", "'")
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw[1:-1]
        return value if isinstance(value, str) else None
    return raw


def _flow_tag_values(raw: str) -> list[str]:
    if not (raw.startswith("[") and raw.endswith("]")):
        value = _tag_value(raw)
        return [value] if value is not None else []
    body = raw[1:-1]
    values, current, quote, escaped = [], [], None, False
    for character in body + ",":
        if escaped:
            current.append(character)
            escaped = False
        elif quote and character == "\\" and quote == '"':
            current.append(character)
            escaped = True
        elif character in "'\"":
            current.append(character)
            quote = None if quote == character else quote or character
        elif character == "," and not quote:
            value = _tag_value("".join(current))
            if value is not None:
                values.append(value)
            current = []
        else:
            current.append(character)
    return values


def _strip_yaml_comment(line: str) -> str:
    quote = None
    index = 0
    while index < len(line):
        character = line[index]
        if quote == "'":
            if character == "'":
                if index + 1 < len(line) and line[index + 1] == "'":
                    index += 2
                    continue
                quote = None
        elif quote == '"':
            if character == "\\":
                index += 2
                continue
            if character == '"':
                quote = None
        elif character in "'\"":
            quote = character
        elif character == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index].rstrip()
        index += 1
    return line.rstrip()


def note_tags(note: Path) -> list[str]:
    """Read supported YAML tag serializations without loading arbitrary YAML."""
    try:
        frontmatter = frontmatter_body(note.read_text())
    except OSError:
        return []
    if frontmatter is None:
        return []
    lines = frontmatter.splitlines()
    values = []
    for index, raw_line in enumerate(lines):
        line = _strip_yaml_comment(raw_line)
        match = re.match(r"^tags\s*:\s*(?P<value>.*)$", line)
        if not match:
            continue
        inline = match.group("value").strip()
        if inline:
            values.extend(_flow_tag_values(inline))
            break
        tag_indent = 0
        for raw_child in lines[index + 1:]:
            child = _strip_yaml_comment(raw_child)
            if not child.strip():
                continue
            item = re.match(r"^(?P<indent>\s+)-\s+(?P<value>.+?)\s*$", child)
            if item and len(item.group("indent")) > tag_indent:
                value = _tag_value(item.group("value"))
                if value is not None:
                    values.append(value)
                continue
            break
        break
    return [tag for tag in normalize_tags(values, warn=True)
            if tag.casefold() not in SOURCE_SERVICES]


def write_atomic_note(note: Path, content: str) -> None:
    note.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{note.name}.", suffix=".tmp", dir=note.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        replace_text(temporary, note)
    finally:
        temporary.unlink(missing_ok=True)


def write_note(
    note: Path,
    source: str,
    images: list[Path],
    metadata: dict | None = None,
    service: str | None = None,
    tags: list[str] | None = None,
) -> None:
    metadata = metadata or {}
    service = service or note.stem.split("-", 1)[0]
    if service == "twitter":
        description = metadata.get("content") if isinstance(metadata.get("content"), str) else ""
    else:
        description = metadata.get("description") or metadata.get("content") or metadata.get("selftext") or ""
    title = note_title(metadata, service)
    username = metadata.get("username") or metadata.get("author")
    author = metadata.get("author") if service == "twitter" else metadata.get("fullname") or username
    published = metadata.get("published") or metadata.get("post_date") or metadata.get("date")
    post_url = metadata.get("post_url") or (
        f"https://www.reddit.com{metadata['permalink']}" if metadata.get("permalink") else source
    )
    if isinstance(post_url, str):
        post_url = published_url(post_url)
    link_property = "xhslink" if service == "rednote" else service

    properties = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"created: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
        f"{link_property}: {json.dumps(post_url, ensure_ascii=False)}",
    ]
    if author:
        properties.append(f"author: {json.dumps(author, ensure_ascii=False)}")
    if username:
        properties.append(f"username: {json.dumps(username, ensure_ascii=False)}")
        if service == "instagram":
            author_url = f"https://www.instagram.com/{username}/"
        elif service == "twitter":
            author_url = metadata.get("author_url") or f"https://x.com/{username}"
        else:
            author_url = f"https://www.reddit.com/user/{username}/"
        properties.append(f"author_url: {json.dumps(author_url, ensure_ascii=False)}")
    elif metadata.get("author_url"):
        properties.append(f"author_url: {json.dumps(metadata['author_url'], ensure_ascii=False)}")
    if published:
        properties.append(f"published: {json.dumps(str(published), ensure_ascii=False)}")
    properties.append("tags:")
    properties.extend(f"  - {json.dumps(tag, ensure_ascii=False)}"
                     for tag in merge_tags([service], tags or []))
    kakera = _existing_kakera_value(note)
    if kakera is not None:
        properties.append(f"{TELEGRAM_RECEIPT}: {json.dumps(kakera, separators=(',', ':'))}")
    properties.append("---")

    links = "\n".join(
        f"![]({Path(os.path.relpath(image, note.parent)).as_posix()})" for image in images
    )
    content = "\n".join(properties) + f"\n\n{description.strip()}\n\n{links}\n"
    write_atomic_note(note, content)


def _source_description(metadata: dict, service: str) -> str:
    if service == "twitter":
        value = metadata.get("content")
    else:
        value = metadata.get("description") or metadata.get("content") or metadata.get("selftext")
    return value.strip() if isinstance(value, str) else ""


def _normalise_source_metadata(name: str, service: str, metadata: dict, url: str) -> dict:
    metadata = metadata if isinstance(metadata, dict) else {}
    if service == "instagram":
        return {**metadata, "_capture_post_id": name.split("-", 1)[1], "post_url": url}
    if service == "twitter":
        author = metadata.get("author")
        author = author if isinstance(author, dict) else {}
        username = author.get("name")
        display_name = author.get("nick")
        content = metadata.get("content")
        return {
            **metadata,
            "content": content if isinstance(content, str) else "",
            "author": display_name or metadata.get("author"),
            "username": username or metadata.get("username"),
            "author_url": (
                f"https://x.com/{username or display_name}"
                if username or display_name else metadata.get("author_url")
            ),
            "published": metadata.get("date") or metadata.get("published"),
            "post_url": url,
            "_capture_post_id": name.split("-", 1)[1],
        }
    return {**metadata, "post_url": url}


def _source_fields(source: dict) -> list[tuple[str, str]]:
    metadata, service, url = source["metadata"], source["service"], source["url"]
    fields = [("URL", published_url(metadata.get("post_url") or url)), ("Title", note_title(metadata, service))]
    username = metadata.get("username") or metadata.get("author")
    author = metadata.get("author") if service == "twitter" else metadata.get("fullname") or username
    author_url = metadata.get("author_url")
    if not author_url and username:
        author_url = (
            f"https://www.instagram.com/{username}/" if service == "instagram" else
            f"https://x.com/{username}" if service == "twitter" else
            f"https://www.reddit.com/user/{username}/"
        )
    published = metadata.get("published") or metadata.get("post_date") or metadata.get("date")
    if author:
        fields.append(("Author", str(author)))
    if username:
        fields.append(("Username", str(username)))
    if author_url:
        fields.append(("Author URL", str(author_url)))
    if published:
        fields.append(("Published", str(published)))
    return fields


def _source_image_links(images: list[Path], note: Path) -> str:
    return "\n".join(
        f"![]({Path(os.path.relpath(image, note.parent)).as_posix()})" for image in images
    )


def write_composed_note(note: Path, sources: list[dict], tags: list[str]) -> None:
    primary = sources[0]
    service = primary["service"]
    metadata = primary["metadata"]
    primary_ok = primary.get("images")
    title = note_title(metadata, service)
    properties = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"created: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if service in {"instagram", "twitter", "reddit", "rednote"}:
        link_property = "xhslink" if service == "rednote" else service
    else:
        link_property = "source"
    properties.append(f"{link_property}: {json.dumps(published_url(primary['url']), ensure_ascii=False)}")
    if primary_ok:
        username = metadata.get("username") or metadata.get("author")
        author = metadata.get("author") if service == "twitter" else metadata.get("fullname") or username
        published = metadata.get("published") or metadata.get("post_date") or metadata.get("date")
        if author:
            properties.append(f"author: {json.dumps(str(author), ensure_ascii=False)}")
        if username:
            properties.append(f"username: {json.dumps(str(username), ensure_ascii=False)}")
            author_url = metadata.get("author_url") or (
                f"https://www.instagram.com/{username}/" if service == "instagram" else
                f"https://x.com/{username}" if service == "twitter" else
                f"https://www.reddit.com/user/{username}/"
            )
            properties.append(f"author_url: {json.dumps(author_url, ensure_ascii=False)}")
        elif metadata.get("author_url"):
            properties.append(f"author_url: {json.dumps(metadata['author_url'], ensure_ascii=False)}")
        if published:
            properties.append(f"published: {json.dumps(str(published), ensure_ascii=False)}")
    properties.append("tags:")
    properties.extend(f"  - {json.dumps(tag, ensure_ascii=False)}" for tag in tags)
    kakera = _existing_kakera_value(note)
    if kakera is not None:
        properties.append(f"{TELEGRAM_RECEIPT}: {json.dumps(kakera, separators=(',', ':'))}")
    properties.append("---")

    body = [_source_description(metadata, service)]
    if primary_ok:
        primary_body = [_source_image_links(primary["images"], note)]
        if primary.get("error"):
            primary_body.append(f"Warning: {primary['error']}")
        body.append("\n\n".join(primary_body))
    else:
        label = "Unknown" if primary["service"] == "unknown" else "Failed"
        body.append(f"## Source 1 — {label}\n- URL: {published_url(primary['url'])}\n- Failure: {primary['error']}")
    for index, source in enumerate(sources[1:], 2):
        heading = source["service"].title() if source["service"] != "rednote" else "小红书"
        section = [f"## Source {index} — {heading}"]
        if source.get("error") and not source.get("images"):
            section.extend((f"- URL: {published_url(source['url'])}", f"- Failure: {source['error']}"))
        else:
            section.extend(f"- {key}: {value}" for key, value in _source_fields(source))
            description = _source_description(source["metadata"], source["service"])
            if description:
                section.extend(("", description))
            links = _source_image_links(source["images"], note)
            if links:
                section.extend(("", links))
            if source.get("error"):
                section.extend(("", f"Warning: {source['error']}"))
        body.append("\n".join(section))
    content = "\n".join(properties) + "\n\n" + "\n\n".join(part for part in body if part) + "\n"
    write_atomic_note(note, content)


def _telegram_frontmatter(text: str) -> tuple[dict[str, object], dict[str, str]]:
    """Read simple top-level JSON frontmatter properties."""
    lines = text.splitlines()
    if not lines or lines[0].rstrip("\r") != "---":
        return {}, {}
    closing = next((i for i, line in enumerate(lines[1:], 1) if line.rstrip("\r") == "---"), None)
    if closing is None:
        return {}, {}
    values, raw = {}, {}
    for line in lines[1:closing]:
        key, separator, value = line.partition(":")
        if not separator or key != key.strip() or not key.strip():
            continue
        key = key.strip()
        raw[key] = value.strip()
        if key == TELEGRAM_RECEIPT:
            continue
        try:
            values[key] = json.loads(value.strip())
        except json.JSONDecodeError:
            values[key] = value.strip().strip("\"'")
    return values, raw


def _strict_json(value: str) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result
    def reject_constant(constant: str) -> object:
        raise ValueError(f"unsupported JSON constant: {constant}")
    try:
        return json.loads(value, object_pairs_hook=pairs, parse_constant=reject_constant)
    except (TypeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("invalid kakera receipt JSON") from error


def _kakera_property(text: str) -> tuple[object | None, int | None, int | None]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None, None, None
    closing = next((i for i, line in enumerate(lines[1:], 1)
                    if line.rstrip("\r\n") == "---"), None)
    if closing is None:
        return None, None, None
    found = []
    for index, line in enumerate(lines[1:closing], 1):
        match = re.match(r"^kakera[ \t]*:", line)
        if match:
            found.append((index, line[match.end():].strip()))
    if len(found) > 1:
        raise ValueError("invalid kakera receipt: duplicate property")
    if not found:
        return None, None, closing
    index, raw = found[0]
    return _strict_json(raw), index, closing


def _validated_kakera(text: str) -> tuple[dict[str, object], int | None, int | None]:
    parsed, index, closing = _kakera_property(text)
    if parsed is None and index is None:
        return {}, index, closing
    if not isinstance(parsed, dict):
        raise ValueError("invalid kakera receipt: kakera must be an object")
    shared = parsed.get("shared")
    if "shared" in parsed and not isinstance(shared, dict):
        raise ValueError("invalid kakera receipt: shared must be an object")
    if isinstance(shared, dict) and "telegram" in shared:
        telegram = shared["telegram"]
        if not isinstance(telegram, dict):
            raise ValueError("invalid kakera receipt: shared.telegram must be an object")
        normalized = {}
        for key, ids in telegram.items():
            try:
                canonical = canonical_telegram_chat_id(key)
            except ValueError as error:
                raise ValueError("invalid kakera receipt: chat ID") from error
            if canonical in normalized:
                raise ValueError("invalid kakera receipt: duplicate chat")
            if (not isinstance(ids, list) or not ids or any(
                    isinstance(item, bool) or not isinstance(item, int) or item <= 0
                    for item in ids)):
                raise ValueError("invalid kakera receipt: message IDs")
            normalized[canonical] = ids
        if normalized != telegram:
            shared["telegram"] = normalized
    return parsed, index, closing


def _telegram_receipt(text: str, chat_id: str | None) -> tuple[dict[str, list[int]], int | None]:
    parsed, _, _ = _validated_kakera(text)
    shared = parsed.get("shared", {})
    telegram = shared.get("telegram", {}) if isinstance(shared, dict) else {}
    if not isinstance(telegram, dict):
        return {}, None
    if chat_id is None:
        return dict(telegram), None
    key = canonical_telegram_chat_id(chat_id)
    return dict(telegram), telegram.get(key)


def _existing_kakera_object(note: Path) -> dict[str, object]:
    value = _existing_kakera_value(note)
    return value if value is not None else {}


def _existing_kakera_value(note: Path) -> dict[str, object] | None:
    if not note.exists():
        return None
    parsed, index, _ = _validated_kakera(note.read_text())
    if index is None:
        return None
    return parsed


def _existing_telegram_receipt(note: Path) -> dict[str, list[int]]:
    """Compatibility name for callers; reads only the nested schema."""
    if not note.exists():
        return {}
    receipt, _ = _telegram_receipt(note.read_text(), None)
    return receipt


def _set_telegram_receipt(text: str, chat_id: str, message_ids: list[int]) -> str:
    parsed, index, closing = _validated_kakera(text)
    chat_id = canonical_telegram_chat_id(chat_id)
    if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in message_ids):
        raise ValueError("invalid Telegram message IDs")
    shared = parsed.setdefault("shared", {})
    if not isinstance(shared, dict):
        raise ValueError("invalid kakera receipt: shared must be an object")
    telegram = shared.setdefault("telegram", {})
    if not isinstance(telegram, dict):
        raise ValueError("invalid kakera receipt: shared.telegram must be an object")
    telegram[chat_id] = message_ids
    value = f"kakera: {json.dumps(parsed, ensure_ascii=False, separators=(',', ':'))}"
    lines = text.splitlines(keepends=True)
    if index is not None and closing is not None:
        ending = "\r\n" if lines[index].endswith("\r\n") else "\n" if lines[index].endswith("\n") else ""
        lines[index] = value + ending
        return "".join(lines)
    if closing is not None:
        ending = "\r\n" if lines[closing].endswith("\r\n") else "\n"
        lines.insert(closing, value + ending)
        return "".join(lines)
    return f"---\n{value}\n---\n" + text


def _write_telegram_receipt(note: Path, original: str, updated: str) -> None:
    if note.read_text() != original:
        raise OSError("note changed during Telegram delivery; receipt not written")
    write_atomic_note(note, updated)


def _telegram_image_url(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return path.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".heic", ".tif", ".tiff"))


def _telegram_image_index(vault: Path) -> dict[str, list[Path]]:
    vault = vault.resolve()
    index: dict[str, list[Path]] = {}
    for path in vault.rglob("*"):
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(vault) or not resolved.is_file() or not image_extension(resolved):
                continue
            bucket = index.setdefault(resolved.name, [])
            if resolved not in bucket:
                bucket.append(resolved)
        except OSError:
            continue
    return index


def _telegram_note_images(note: Path, vault: Path, image_index: dict[str, list[Path]] | None = None) -> list[Path]:
    text = note.read_text()
    embeds = list(re.finditer(
        r"!\[\[[^\]]*\]\]|!\[[^\]]*\]\([^)]*\)", text
    ))
    vault = vault.resolve()
    result, seen = [], set()
    oversized = overflow = 0
    for embed in embeds:
        token = embed.group(0)
        if token.startswith("![["):
            target = token[3:-2].split("|", 1)[0].split("#", 1)[0].strip()
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or target.startswith("data:"):
                continue
            if "/" not in target and "\\" not in target:
                candidates = (image_index or _telegram_image_index(vault)).get(Path(target).name, [])
                if len(candidates) > 1:
                    print(f"warning: ambiguous Obsidian image embed omitted: {target}", file=sys.stderr)
                    continue
            else:
                candidates = [vault / target.lstrip("/")]
        else:
            match = re.match(r"!\[[^\]]*\]\((?:<([^>]+)>|([^ )]+))", token)
            target = next((value for value in match.groups() if value is not None), "") if match else ""
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or target.startswith("data:"):
                continue
            candidates = [note.parent / unquote(parsed.path)]
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                if not resolved.is_relative_to(vault) or not resolved.is_file() or resolved in seen:
                    continue
                if not image_extension(resolved):
                    continue
                seen.add(resolved)
                if resolved.stat().st_size > TELEGRAM_MAX_BYTES:
                    oversized += 1
                    continue
                if len(result) == TELEGRAM_MAX_IMAGES:
                    overflow += 1
                    continue
                result.append(resolved)
                break
            except OSError:
                continue
    if oversized:
        print(f"warning: skipped {oversized} Telegram image(s) over 10 MB", file=sys.stderr)
    if overflow:
        print(f"warning: skipped {overflow} eligible Telegram image(s) after the first 10", file=sys.stderr)
    return result


def _telegram_note_caption(note: Path, text: str, selected_url: str | None = None) -> str:
    values, _ = _telegram_frontmatter(text)
    url = selected_url
    if not url:
        for key in ("source", "instagram", "twitter", "reddit", "xhslink", "url"):
            value = values.get(key)
            if isinstance(value, str) and re.match(r"^https?://", value, re.I):
                url = value
                break
    if not url:
        body = text.split("---", 2)[-1] if text.startswith("---") else text
        body = re.sub(r"!\[[^\]]*\]\([^)]*\)|!\[\[[^\]]*\]\]", "", body)
        url = next((match.group(0).rstrip(".,!?;:)]}") for match in HTTP_URL.finditer(body)
                    if not _telegram_image_url(match.group(0))), None)
    return _telegram_caption_text(
        note.stem, published_url(url) if url else url, note.name
    )


def telegram_sender_name(sender: dict) -> str | None:
    username = sender.get("username")
    if isinstance(username, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username):
        return f"@{username}"
    parts = []
    for key in ("first_name", "last_name"):
        value = sender.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(re.sub(r"[\r\n]+", " ", value).strip())
    name = " ".join(parts).strip()
    return name or None


def _telegram_caption_text(stem: str, url: str | None, label: str = "note",
                           sender: str | None = None) -> str:
    stem = stem.replace("\r", " ").replace("\n", " ")
    credit = ("from " + sender.replace("\r", " ").replace("\n", " ")) if sender else None
    lines = [stem]
    if url:
        candidate = "\n".join(lines + [url] + ([credit] if credit else []))
        if len(candidate) <= 1024:
            return candidate
        print(f"warning: Telegram caption URL omitted because it does not fit: {label}", file=sys.stderr)
    if credit:
        candidate = f"{stem}\n{credit}"
        if len(candidate) <= 1024:
            return candidate
        return candidate[:1024]
    return stem[:1024]


def _telegram_file_kind(path: Path) -> str | None:
    if image_extension(path):
        return "photo"
    if video_extension(path):
        return "video"
    return None


def _telegram_file_extension(path: Path) -> str:
    return image_extension(path) or video_extension(path) or ".bin"


def _telegram_count_label(paths: list[Path]) -> str:
    images = sum(1 for path in paths if image_extension(path))
    videos = sum(1 for path in paths if video_extension(path))
    parts = []
    if images:
        parts.append(f"{images} image" if images == 1 else f"{images} images")
    if videos:
        parts.append(f"{videos} video" if videos == 1 else f"{videos} videos")
    return " and ".join(parts) if parts else "0 images"


def _telegram_filter_images(paths: list[Path], *, allow_video: bool = False) -> list[Path]:
    result, seen = [], set()
    oversized_images = oversized_videos = overflow = 0
    for path in paths:
        try:
            path = path.resolve()
            if not path.is_file() or path in seen or not media_extension(path, allow_video=allow_video):
                continue
            seen.add(path)
            video = bool(video_extension(path))
            limit = TELEGRAM_MAX_VIDEO_BYTES if video else TELEGRAM_MAX_BYTES
            if path.stat().st_size > limit:
                if video:
                    oversized_videos += 1
                else:
                    oversized_images += 1
                continue
            if len(result) >= TELEGRAM_MAX_IMAGES:
                overflow += 1
                continue
            result.append(path)
        except OSError:
            continue
    if oversized_images:
        print(f"warning: skipped {oversized_images} Telegram image(s) over 10 MB", file=sys.stderr)
    if oversized_videos:
        print(f"warning: skipped {oversized_videos} Telegram video(s) over 50 MB", file=sys.stderr)
    if overflow:
        kind = "image(s) or video" if allow_video else "image(s)"
        print(f"warning: skipped {overflow} eligible Telegram {kind} after the first 10", file=sys.stderr)
    return result


def _telegram_multipart(method: str, chat_id: str, caption: str, images: list[Path]) -> tuple[bytes, str]:
    boundary = f"kakera{hashlib.sha256(os.urandom(16)).hexdigest()}"
    chunks = []
    def field(name: str, value: str):
        chunks.extend((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode(),))
    field("chat_id", chat_id)
    if method == "sendPhoto":
        field("caption", caption)
        path = images[0]
        data = path.read_bytes()
        chunks.extend((f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"photo{image_extension(path) or '.bin'}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode(), data, b"\r\n"))
    elif method == "sendVideo":
        field("caption", caption)
        path = images[0]
        data = path.read_bytes()
        chunks.extend((f"--{boundary}\r\nContent-Disposition: form-data; name=\"video\"; filename=\"video{_telegram_file_extension(path)}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode(), data, b"\r\n"))
    else:
        media = []
        for i, path in enumerate(images):
            kind = _telegram_file_kind(path) or "photo"
            item = {"type": kind, "media": f"attach://file{i}"}
            if i == 0:
                item["caption"] = caption
            media.append(item)
        field("media", json.dumps(media, separators=(",", ":")))
        for i, path in enumerate(images):
            chunks.extend((f"--{boundary}\r\nContent-Disposition: form-data; name=\"file{i}\"; filename=\"file{i}{_telegram_file_extension(path)}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode(), path.read_bytes(), b"\r\n"))
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), boundary


def telegram_send(chat_id: str, caption: str, images: list[Path], token: str | None = None) -> list[int]:
    if not 1 <= len(images) <= TELEGRAM_MAX_IMAGES:
        raise ValueError("Telegram delivery requires 1 to 10 images")
    if len(images) == 1:
        method = "sendVideo" if video_extension(images[0]) else "sendPhoto"
    else:
        method = "sendMediaGroup"
    body, boundary = _telegram_multipart(method, chat_id, caption, images)
    token = token or telegram_token()
    timeout = 120 if any(video_extension(path) for path in images) else 30
    request = Request(
        f"{TELEGRAM_API}/bot{token}/{method}", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            if getattr(response, "status", 200) >= 400:
                raise OSError("Telegram HTTP error")
            payload = json.loads(response.read() or b"{}")
    except HTTPError as error:
        try:
            payload = json.loads(error.read() or b"{}")
        except (OSError, json.JSONDecodeError, TypeError):
            payload = {}
        raise _telegram_api_error(method, payload, error) from error
    except (OSError, json.JSONDecodeError, TypeError) as error:
        raise _telegram_api_error(method, error=error) from error
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise _telegram_api_error(method, payload)
    result = payload.get("result")
    messages = result if method == "sendMediaGroup" else [result]
    if (not isinstance(messages, list) or len(messages) != len(images)
            or any(not isinstance(item, dict)
                   or isinstance(item.get("message_id"), bool)
                   or not isinstance(item.get("message_id"), int)
                   or item["message_id"] <= 0 for item in messages)):
        raise ValueError("Telegram returned an invalid delivery response")
    return [item["message_id"] for item in messages]


def publish_telegram_note(note: Path, vault: Path, *, selected_url: str | None = None,
                          manual: bool = False, relative_root: Path | None = None,
                          image_index: dict[str, list[Path]] | None = None) -> tuple[bool, str]:
    chat_id = configured_telegram_chat_id()
    with capture_lock(note.parent, "telegram", note.name):
        text = note.read_text()
        receipt, current = _telegram_receipt(text, chat_id)
        if current:
            if not manual:
                return True, f"Telegram already sent to {chat_id}"
            images = _telegram_note_images(note, vault, image_index)
            if not images:
                raise ValueError(f"{note.name}: no eligible local images")
            token = telegram_token()
            label = note.relative_to(relative_root) if relative_root else note.relative_to(note.parent)
            prompt = f"{label} was already sent to Telegram chat {chat_id}. Send again? [y/N] "
            if not sys.stdin.isatty():
                raise ValueError("cannot confirm Telegram resend without an interactive terminal")
            print(prompt, file=sys.stderr, end="", flush=True)
            answer = sys.stdin.readline()
            if not answer:
                raise ValueError("cannot confirm Telegram resend: end of input")
            if answer.strip().casefold() not in {"y", "yes"}:
                return True, "resend declined"
        else:
            images = _telegram_note_images(note, vault, image_index)
            if not images:
                raise ValueError(f"{note.name}: no eligible local images")
            token = telegram_token()
        caption = _telegram_note_caption(note, text, selected_url)
        # ponytail: network stays under the note lock; split decision/send/update only if throughput matters.
        try:
            message_ids = telegram_send(chat_id, caption, images, token)
        except ValueError as error:
            return False, _telegram_delivery_message(error)
        # Re-read after Telegram acknowledges so concurrent Obsidian edits survive receipt writeback.
        try:
            latest = note.read_text()
            updated = _set_telegram_receipt(latest, chat_id, message_ids)
            _write_telegram_receipt(note, latest, updated)
        except (OSError, ValueError) as error:
            return False, f"Telegram sent but receipt update failed: {error}"
    return True, f"Telegram sent {len(message_ids)} image(s) to {chat_id}"


def _telegram_note_files(notes: Path) -> list[Path]:
    notes = notes.resolve()
    try:
        return [path.resolve() for path in notes.rglob("*.md")
                if path.resolve().is_relative_to(notes) and path.resolve().is_file()]
    except OSError as error:
        raise ValueError(f"cannot scan Obsidian notes: {error}") from error


def _telegram_note_candidates(selector: str, notes: Path, vault: Path,
                              files: list[Path] | None = None) -> tuple[list[Path], str | None]:
    notes = notes.resolve()
    vault = vault.resolve()
    # ponytail: O(n) recursive scan per command; command mode supplies one scan-built file list.
    files = files if files is not None else _telegram_note_files(notes)
    selector = selector.strip()
    if re.match(r"^https?://", selector, re.I):
        matches = []
        for path in files:
            values, _ = _telegram_frontmatter(path.read_text())
            for key, value in values.items():
                if key in {"source", "instagram", "twitter", "reddit", "xhslink", "url"} and isinstance(value, str) and re.match(r"^https?://", value, re.I) and canonical_url(value) == canonical_url(selector):
                    matches.append(path)
                    break
        return matches, normalize_instagram_post_url(selector) if matches else None
    wikilink = selector.startswith("[[") and selector.endswith("]]" )
    if wikilink:
        selector = selector[2:-2].split("|", 1)[0].strip()
    path_matches = []
    raw_path = Path(selector)
    wikilink_path = wikilink and ("/" in selector or "\\" in selector)
    exact_path_selector = (not wikilink and raw_path.suffix.casefold() == ".md") or wikilink_path
    qualified_path = raw_path.is_absolute() or len(raw_path.parts) > 1
    if exact_path_selector:
        candidate = raw_path if raw_path.is_absolute() else notes / raw_path
        if wikilink and candidate.suffix.casefold() != ".md":
            candidate = candidate.with_suffix(".md")
        try:
            candidate = candidate.resolve()
            if candidate.is_file() and candidate.suffix.casefold() == ".md" and candidate.is_relative_to(notes):
                path_matches = [candidate]
        except OSError:
            pass
    if path_matches or qualified_path:
        return path_matches, None
    stem = Path(selector).stem if selector.casefold().endswith(".md") else selector
    matches = [path for path in files if path.name == selector or path.stem == stem]
    return matches, None


def telegram_command(selectors: list[str]) -> int:
    notes, _ = configured_folders()
    notes = notes.resolve()
    config = read_config().get("obsidian", {})
    vault = Path(config["vault"]).expanduser().resolve()
    if not notes.is_relative_to(vault):
        raise ValueError("configured Obsidian notes folder must stay inside the vault")
    files = _telegram_note_files(notes)
    image_index = _telegram_image_index(vault)
    failed = False
    for selector in selectors:
        try:
            matches, selected_url = _telegram_note_candidates(selector, notes, vault, files)
            if not matches:
                raise ValueError(f"no note matched {selector!r}")
            if len(matches) > 1:
                relative = ", ".join(str(path.relative_to(notes)) for path in matches)
                raise ValueError(f"ambiguous note {selector!r}: {relative}")
            note = matches[0]
            success, message = publish_telegram_note(
                note, vault, selected_url=selected_url, manual=True, relative_root=notes,
                image_index=image_index
            )
            print(f"{'ok' if success else 'error'}: {note.relative_to(notes)}: {message}")
            failed |= not success
        except KeyboardInterrupt:
            print("error: Telegram resend cancelled", file=sys.stderr)
            return 130
        except EOFError:
            print("error: Telegram resend cancelled", file=sys.stderr)
            failed = True
        except (OSError, ValueError) as error:
            print(f"error: {selector}: {error}", file=sys.stderr)
            failed = True
    return int(failed)


def telegram_note_only_command(selectors: list[str]) -> int:
    """Publish selected notes without consulting or changing Telegram receipts."""
    chat_id = configured_telegram_chat_id()
    token = telegram_token()
    notes, _ = configured_folders()
    notes = notes.resolve()
    config = read_config().get("obsidian", {})
    vault = Path(config["vault"]).expanduser().resolve()
    if not notes.is_relative_to(vault):
        raise ValueError("configured Obsidian notes folder must stay inside the vault")
    files = _telegram_note_files(notes)
    image_index = _telegram_image_index(vault)
    failed = False
    for selector in selectors:
        try:
            matches, selected_url = _telegram_note_candidates(selector, notes, vault, files)
            if not matches:
                raise ValueError(f"no note matched {selector!r}")
            if len(matches) > 1:
                relative = ", ".join(str(path.relative_to(notes)) for path in matches)
                raise ValueError(f"ambiguous note {selector!r}: {relative}")
            note = matches[0]
            text = note.read_text()
            images = _telegram_note_images(note, vault, image_index)
            if not images:
                raise ValueError(f"{note.name}: no eligible local images")
            try:
                message_ids = telegram_send(
                    chat_id, _telegram_note_caption(note, text, selected_url), images, token
                )
            except ValueError as error:
                raise ValueError(_telegram_delivery_message(error)) from error
            message = f"Telegram sent {len(message_ids)} image(s) to {chat_id}"
            print(f"ok: {note.relative_to(notes)}: {message}")
        except KeyboardInterrupt:
            print("error: Telegram send cancelled", file=sys.stderr)
            return 130
        except (OSError, ValueError) as error:
            print(f"error: {selector}: {error}", file=sys.stderr)
            failed = True
    return int(failed)


def telegram_only_url(url: str, browser: str | None, account: str | None = None,
                      twitter_account: str | None = None, *,
                      sender: str | None = None) -> tuple[bool, str]:
    """Fetch one URL to a temporary directory and publish it without saving."""
    chat_id = configured_telegram_chat_id()
    token = telegram_token()
    try:
        name = capture_id(url)
    except ValueError as error:
        return False, f"Telegram not sent: {error}"
    with tempfile.TemporaryDirectory(prefix="kakera-telegram-") as directory:
        source, error = fetch_source(url, browser, account, twitter_account,
                                     Path(directory), name, include_video=True)
        source_error = error or (source.get("error") if isinstance(source, dict) else None)
        if source_error or not source:
            return False, f"Telegram not sent: {source_error or 'source fetch failed'}"
        temporary_root = Path(directory).resolve()
        candidates = []
        for path, _ in source.get("valid", []):
            try:
                if path.resolve().is_relative_to(temporary_root):
                    candidates.append(path)
            except OSError:
                pass
        files = _telegram_filter_images(candidates, allow_video=True)
        if not files:
            return False, "Telegram not sent: no eligible local images or video"
        metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        stem = note_filename(metadata, source.get("service", "unknown"))
        caption = _telegram_caption_text(
            stem, published_url(source.get("url") or url), url, sender=sender
        )
        try:
            telegram_send(chat_id, caption, files, token)
        except ValueError as error:
            return False, _telegram_delivery_message(error)
        label = _telegram_count_label(files)
    return True, f"Telegram sent {label} to {chat_id}; nothing saved"


def telegram_requested(tags: list[str] | None) -> bool:
    return any(isinstance(tag, str) and tag.casefold() == TELEGRAM_TAG for tag in tags or [])


def telegram_only_requested(tags: list[str] | None) -> bool:
    return any(isinstance(tag, str) and tag.casefold() == TELEGRAM_ONLY_TAG for tag in tags or [])


def process_transient_urls(
    urls: list[str],
    browser: str | None,
    account: str | None,
    twitter_account: str | None,
    *,
    origin: str = "",
    reported: dict | None = None,
    named_failures: dict | None = None,
) -> bool:
    failed = False
    for url in urls:
        success, message = telegram_only_url(url, browser, account, twitter_account)
        log_queue_result(reported, [url], success, message)
        capture_key = ("capture", (url,))
        if success:
            if reported is not None:
                reported.pop(capture_key, None)
            continue
        reason = instagram_named_error(message)
        if reason and named_failures is not None:
            named_failures.setdefault(reason, []).append(url)
        elif origin:
            report_queue_failure(
                origin,
                f"{published_url(url)}: {message}",
                key=capture_key,
                reported=reported,
            )
        failed |= not success
    return not failed


def capture_telegram_vault(notes: Path) -> Path:
    try:
        configured = read_config().get("obsidian", {}).get("vault")
        if configured:
            vault = Path(configured).expanduser().resolve()
            if notes.resolve().is_relative_to(vault):
                return vault
    except (AttributeError, TypeError):
        pass
    return notes.parent.resolve()


def publish_capture_telegram(note: Path, notes: Path) -> tuple[bool, str]:
    try:
        return publish_telegram_note(note, capture_telegram_vault(notes))
    except (OSError, ValueError) as error:
        return False, str(error)


def save(
    url: str,
    browser: str | None,
    notes: Path = ROOT / "downloads",
    attachment_root: Path = ROOT / "attachments",
    account: str | None = None,
    twitter_account: str | None = None,
    tags: list[str] | None = None,
) -> tuple[bool, str]:
    try:
        name = capture_id(url)
    except ValueError as error:
        return False, str(error)
    with tempfile.TemporaryDirectory(prefix="kakera-") as directory:
        source, error = fetch_source(url, browser, account, twitter_account, Path(directory), name)
        if error:
            return False, error
        try:
            stored = store_source_images(source, notes, attachment_root)
            if not stored:
                cleanup_empty_attachment_dirs([source], attachment_root)
                return False, source.get("error") or "no supported images found"
            if source.get("error"):
                return False, source["error"]
            notes.mkdir(parents=True, exist_ok=True)
            with capture_lock(notes, source["service"], source["name"]):
                note = note_path(notes, source["name"], source["metadata"], url)
                write_note(note, url, stored, source["metadata"], source["service"],
                           merge_tags(note_tags(note), tags or []))
            if telegram_requested(tags):
                delivered, delivery_message = publish_capture_telegram(note, notes)
                if not delivered:
                    return False, f"capture saved; Telegram failed: {delivery_message}"
                return True, f"saved {len(stored)} image(s); {delivery_message}"
        except (OSError, ValueError) as error:
            return False, f"cannot save capture: {error}"
        return True, f"saved {len(stored)} image(s)"


def fetch_source(
    url: str,
    browser: str | None,
    account: str | None,
    twitter_account: str | None,
    directory: Path,
    name: str | None = None,
    include_video: bool = False,
) -> tuple[dict | None, str | None]:
    directory.mkdir(parents=True, exist_ok=True)
    try:
        name = name or capture_id(url)
    except ValueError as error:
        return None, str(error)
    normalized_url = normalize_instagram_post_url(url)
    metadata: dict = {}
    result = None
    if name.startswith("rednote-"):
        try:
            name, metadata = download_rednote(url, directory, include_video=include_video)
        except ValueError as error:
            return None, str(error)
    else:
        command = [
            sys.executable, "-m", "gallery_dl", "--config-ignore", "--no-input",
            "--filesize-max", "50M", "--directory", directory,
            "--Print", "after:{_path}",
            "--write-metadata",
        ]
        if name.startswith("instagram-") and account:
            cookies = instagram_cookie_path(account)
            if not cookies.is_file():
                return None, f"Instagram account {account!r} has no saved cookies"
            command.extend(("--cookies", str(cookies)))
        elif name.startswith("twitter-") and twitter_account:
            cookies = twitter_cookie_path(twitter_account)
            if not cookies.is_file():
                return None, f"Twitter account {twitter_account!r} has no saved cookies"
            command.extend(("--cookies", str(cookies)))
        elif browser:
            command.extend(("--cookies-from-browser", browser))
        if name.startswith("reddit-"):
            try:
                reddit = configured_reddit()
            except ValueError as error:
                return None, str(error)
            if reddit:
                command.extend(("-o", f"extractor.reddit.client-id={reddit[0]}",
                                "-o", f"extractor.reddit.user-agent={reddit[1]}"))
        if not include_video:
            service = name.split("-", 1)[0]
            if service in {"instagram", "twitter", "reddit"}:
                command.extend(("-o", f"extractor.{service}.videos=false"))
        command.append(normalized_url)
        result = subprocess.run(command, capture_output=True, text=True)
    if result and result.returncode:
        error = instagram_fetch_error(result.stderr or "")
        if not error:
            error = next((line for line in reversed(result.stderr.splitlines()) if line.strip()), "download failed")
        if error not in {INSTAGRAM_SESSION_EXPIRED, INSTAGRAM_FOLLOWERS_ONLY} and "[reddit]" in error:
            try:
                configured = configured_reddit()
            except ValueError:
                configured = True
            if not configured:
                error += f"; add reddit.client_id and reddit.user_agent to {CONFIG.name}"
        return None, error
    extraction_root = directory.resolve()

    def contained_file(path: Path) -> Path | None:
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(extraction_root) or not resolved.is_file():
                return None
            return resolved
        except OSError:
            return None

    if name.startswith("rednote-"):
        candidates = []
        try:
            entries = list(directory.iterdir())
        except OSError:
            entries = []
        for path in sorted(entries, key=lambda item: item.name):
            candidate = contained_file(path)
            if candidate is not None:
                candidates.append(candidate)
    else:
        candidates = [contained_file(path) for path in directory.rglob("*")]
        candidates = [path for path in candidates if path is not None]
        printed = []
        if result and result.stdout:
            for line in result.stdout.splitlines():
                candidate = Path(line.strip())
                if not candidate.is_absolute():
                    candidate = directory / candidate
                candidate = contained_file(candidate)
                if candidate is not None and candidate not in printed:
                    printed.append(candidate)
        candidates = printed + [path for path in candidates if path not in printed]
    valid = []
    for path in candidates:
        extension = media_extension(path, allow_video=include_video)
        if extension:
            valid.append((path, extension))
    if not valid:
        return None, ("no supported images or video found" if include_video
                      else "no supported images found")
    if not metadata:
        sidecars = [path.with_suffix(path.suffix + ".json") for path, _ in valid]
        sidecars = [contained_file(path) for path in sidecars]
        sidecars = [path for path in sidecars if path is not None]
        sidecars.extend(path for path in candidates if path.suffix == ".json" and path not in sidecars)
        for path in sidecars:
            try:
                loaded = json.loads(path.read_text())
                if isinstance(loaded, dict):
                    metadata = loaded
                    break
            except (OSError, json.JSONDecodeError):
                pass
    service = name.split("-", 1)[0]
    published = published_url(normalized_url)
    metadata = _normalise_source_metadata(name, service, metadata, published)
    return {"name": name, "service": service, "url": published,
            "metadata": metadata, "valid": valid}, None


def store_source_images(source: dict, notes: Path, attachment_root: Path) -> list[Path]:
    with capture_lock(attachment_root, source["service"], source["name"]):
        return _store_source_images(source, notes, attachment_root)


def _store_source_images(source: dict, notes: Path, attachment_root: Path) -> list[Path]:
    service, name = source["service"], source["name"]
    attachments = attachment_root / service
    existing = []
    try:
        candidates = list(attachments.glob(f"{name}-*.*"))
        for path in candidates:
            try:
                if image_extension(path):
                    existing.append(path)
            except OSError as error:
                source["error"] = f"cannot inspect attachments: {error}"
    except OSError as error:
        source["error"] = f"cannot inspect attachments: {error}"
        source["images"] = []
        return source["images"]
    existing.sort(key=lambda path: int(match.group(1)) if (match := re.fullmatch(
        rf"{re.escape(name)}-(\d+)\.[^.]+", path.name)) else 10**9)
    hashes = set()
    readable = []
    for path in existing:
        try:
            hashes.add(digest(path))
            readable.append(path)
        except OSError as error:
            source["error"] = f"cannot read existing attachment: {error}"
    existing = readable
    source["images"] = existing
    numbers = [int(match.group(1)) for path in existing
               if (match := re.fullmatch(rf"{re.escape(name)}-(\d+)\.[^.]+", path.name))]
    next_number = max(numbers, default=0) + 1
    for incoming, extension in source["valid"]:
        try:
            source_hash = digest(incoming)
        except OSError as error:
            source["error"] = f"cannot read downloaded image: {error}"
            break
        if source_hash in hashes:
            continue
        try:
            source["_created_attachment_dir"] = not attachments.exists()
            attachments.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            source["error"] = f"cannot create attachment folder: {error}"
            break
        temporary_name = None
        published = False
        try:
            for _attempt in range(1000):
                target = attachments / f"{name}-{next_number:02}{extension}"
                with tempfile.NamedTemporaryFile(
                    prefix=f".{name}-", suffix=extension, dir=attachments, delete=False
                ) as target_file:
                    temporary_name = Path(target_file.name)
                    with incoming.open("rb") as source_file:
                        shutil.copyfileobj(source_file, target_file)
                    target_file.flush()
                    os.fsync(target_file.fileno())
                try:
                    os.link(temporary_name, target)
                except FileExistsError:
                    temporary_name.unlink(missing_ok=True)
                    temporary_name = None
                    try:
                        if digest(target) == source_hash:
                            if target not in existing:
                                existing.append(target)
                            hashes.add(source_hash)
                            published = True
                            break
                    except OSError:
                        pass
                    next_number += 1
                    continue
                temporary_name.unlink(missing_ok=True)
                existing.append(target)
                hashes.add(source_hash)
                next_number += 1
                published = True
                break
            if not published:
                source["error"] = "cannot reserve a unique attachment name"
        except OSError as error:
            source["error"] = f"cannot save attachments: {error}"
        finally:
            if temporary_name:
                temporary_name.unlink(missing_ok=True)
        if not published:
            break
    source["images"] = existing
    return source["images"]


def cleanup_empty_attachment_dirs(sources: list[dict], attachment_root: Path) -> None:
    with capture_lock(attachment_root, "cleanup", "root"):
        for source in sources:
            if not source.get("_created_attachment_dir"):
                continue
            path = attachment_root / source["service"]
            try:
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            except OSError:
                pass
        try:
            if attachment_root.is_dir() and not any(attachment_root.iterdir()):
                attachment_root.rmdir()
        except OSError:
            pass


def composed_note_path(notes: Path, primary: dict) -> Path:
    canonical = canonical_url(primary["url"])
    property_key = {"rednote": "xhslink"}.get(primary["service"], primary["service"])
    if primary["service"] not in {"instagram", "twitter", "reddit", "rednote"}:
        property_key = "source"
    for path in notes.glob("*.md"):
        try:
            frontmatter = frontmatter_body(path.read_text())
            if frontmatter is None:
                continue
            for line in frontmatter.splitlines():
                key, separator, raw_value = line.partition(":")
                if separator and key.strip() == property_key:
                    try:
                        value = json.loads(raw_value.strip())
                    except json.JSONDecodeError:
                        value = ""
                    if isinstance(value, str) and canonical_url(value) == canonical:
                        return path
                    break
        except (OSError, ValueError):
            continue
    return note_path(notes, primary["name"], primary["metadata"], primary["url"])


def save_composed(
    urls: list[str], browser: str | None,
    notes: Path = ROOT / "downloads", attachment_root: Path = ROOT / "attachments",
    account: str | None = None, twitter_account: str | None = None,
    tags: list[str] | None = None,
    named_failures: dict | None = None,
) -> tuple[bool, str]:
    unique = dedupe_urls(urls)
    if not unique:
        return False, "provide at least one URL"
    if len(unique) == 1:
        success, message = (
            save(unique[0], browser, notes, attachment_root, account, twitter_account, tags)
            if tags else
            save(unique[0], browser, notes, attachment_root, account, twitter_account)
        )
        reason = instagram_named_error(message)
        if reason and named_failures is not None:
            named_failures.setdefault(reason, []).append(unique[0])
        return success, message
    with tempfile.TemporaryDirectory(prefix="kakera-compose-") as directory:
        sources = []
        for index, url in enumerate(unique):
            try:
                name = capture_id(url)
            except ValueError as capture_error:
                name = f"unknown-{hashlib.sha256(canonical_url(url).encode()).hexdigest()[:12]}"
                error = str(capture_error)
            else:
                error = None
            if error:
                sources.append({"name": name or "unknown", "service": "unknown", "url": url,
                                "metadata": {"_capture_post_id": hashlib.sha256(canonical_url(url).encode()).hexdigest()[:12]},
                                "error": error})
                continue
            source, error = fetch_source(url, browser, account, twitter_account,
                                         Path(directory) / str(index), name)
            if error:
                service = name.split("-", 1)[0] if name else "unknown"
                metadata = {"_capture_post_id": name.split("-", 1)[1]} if name and "-" in name else {}
                sources.append({"name": name or "unknown", "service": service, "url": url,
                                "metadata": metadata, "error": error})
            else:
                sources.append(source)
        found: dict[str, list[str]] = {}
        for source in sources:
            reason = instagram_named_error(source.get("error"))
            if reason:
                found.setdefault(reason, []).append(source["url"])
        if named_failures is not None:
            for reason, urls in found.items():
                named_failures.setdefault(reason, []).extend(urls)
        successful = [source for source in sources if source.get("valid")]
        if not successful:
            named_text = format_named_instagram(found)
            unnamed = sum(1 for source in sources if not instagram_named_error(source.get("error")))
            if named_text and not unnamed:
                return False, named_text
            if named_text:
                return False, f"no sources produced supported images; {named_text}"
            return False, "no sources produced supported images"
        try:
            for source in successful:
                try:
                    store_source_images(source, notes, attachment_root)
                except OSError as error:
                    source["error"] = f"cannot save attachments: {error}"
                    source["images"] = []
            primary = sources[0]
            if not primary.get("valid"):
                primary["metadata"] = {"_capture_post_id": primary["name"].split("-", 1)[-1]}
            service_tags = []
            if primary["service"] in {"instagram", "twitter", "reddit", "rednote"}:
                service_tags.append(primary["service"])
            service_tags.extend(source["service"] for source in sources[1:] if source.get("images")
                        if source["service"] in {"instagram", "twitter", "reddit", "rednote"})
            service_tags = merge_tags(service_tags)
            if not any(source.get("images") for source in sources):
                cleanup_empty_attachment_dirs(sources, attachment_root)
                return False, "cannot save composition: no images persisted"
            notes.mkdir(parents=True, exist_ok=True)
            with capture_lock(notes, primary["service"], primary["name"]):
                note = composed_note_path(notes, primary)
                write_composed_note(note, sources, merge_tags(service_tags, note_tags(note), tags or []))
            if telegram_requested(tags):
                delivered, delivery_message = publish_capture_telegram(note, notes)
                if not delivered:
                    return False, f"capture saved; Telegram failed: {delivery_message}"
                delivery_suffix = f"; {delivery_message}"
            else:
                delivery_suffix = ""
        except (OSError, ValueError) as error:
            return False, f"cannot save composition: {error}"
        failures = sum(1 for source in sources if source.get("error"))
        named_text = format_named_instagram(found)
        bits = []
        if failures:
            bits.append(f"{failures} source(s) failed")
        if named_text:
            bits.append(named_text)
        suffix = ("; " + "; ".join(bits)) if bits else ""
        return True, f"saved {sum(len(source['images']) for source in successful)} image(s){suffix}{delivery_suffix if telegram_requested(tags) else ''}"


def process_inbox(
    inbox: Path,
    browser: str | None,
    notes: Path,
    attachments: Path,
    account: str | None = None,
    completed: set[str] | None = None,
    report_empty: bool = True,
    twitter_account: str | None = None,
    tags: list[str] | None = None,
    reported: dict | None = None,
) -> int:
    if not inbox.exists():
        inbox.parent.mkdir(parents=True, exist_ok=True)
        inbox.write_text("# Kakera Inbox\n\n")
        stamp_print(f"ok: created {inbox}")
        return 0

    completed = completed if completed is not None else set()
    failed = False
    named: dict[str, list[str]] = {}
    attempted: set[tuple] = set()
    groups = pending_inbox_groups(inbox)
    if not groups and report_empty:
        print("ok: no pending links")
        return 0

    for group in groups:
        signature = inbox_group_signature(group)
        if signature in attempted:
            continue
        attempted.add(signature)
        if signature in completed:
            if not mark_completed_signature(inbox, signature):
                completed.remove(signature)
                if reported is not None:
                    reported.pop(("close", signature), None)
            else:
                failed = True
                report_queue_failure(
                    "Inbox",
                    f"captured but not checked in {inbox}",
                    key=("close", signature),
                    reported=reported,
                )
            continue
        urls = group["urls"]
        capture_tags = merge_tags(group.get("tags", []), tags or [])
        capture_key = ("capture", tuple(urls))
        if telegram_only_requested(capture_tags):
            success = process_transient_urls(
                urls, browser, account, twitter_account,
                origin="Inbox", reported=reported, named_failures=named,
            )
            if success:
                completion_failed = mark_completed_signature(inbox, signature)
                failed |= completion_failed
                if completion_failed:
                    completed.add(signature)
                    report_queue_failure(
                        "Inbox",
                        f"captured but not checked in {inbox}",
                        key=("close", signature),
                        reported=reported,
                    )
                elif reported is not None:
                    reported.pop(("close", signature), None)
            failed |= not success
            continue
        success, message = save_composed(
            urls, browser, notes, attachments, account, twitter_account,
            capture_tags or None, named_failures=named,
        )
        log_queue_result(reported, urls, success, message)
        if success:
            if reported is not None:
                reported.pop(capture_key, None)
            completion_failed = mark_completed_signature(inbox, signature)
            failed |= completion_failed
            if completion_failed:
                completed.add(signature)
                report_queue_failure(
                    "Inbox",
                    f"captured but not checked in {inbox}",
                    key=("close", signature),
                    reported=reported,
                )
            elif reported is not None:
                reported.pop(("close", signature), None)
        elif instagram_named_error(message) is None or "no sources produced" in message:
            report_queue_failure(
                "Inbox",
                f"{', '.join(published_url(url) for url in urls)}: {message}",
                key=capture_key,
                reported=reported,
            )
        failed |= not success
    report_named_instagram_failures("Inbox", named, reported)
    return int(failed)


def read_inbox(inbox: Path) -> str:
    try:
        return inbox.read_text()
    except OSError as error:
        if error.errno == errno.EPERM:
            raise ValueError(
                f"macOS blocked access to {inbox}; this iCloud File Provider path "
                "is not available to Kakera. Use a local vault or a normal iCloud "
                "Drive path visible in Finder"
            ) from error
        raise


def pending_urls(inbox: Path) -> list[str]:
    return list(dict.fromkeys(
        match.group(1)
        for line in read_inbox(inbox).splitlines()
        if (match := INBOX_TASK.match(line))
    ))


TASK_LINE = re.compile(r"^(?P<indent>\s*)-\s+\[(?P<done>[ xX])\]\s+(?P<text>.*)$")


def pending_inbox_groups(inbox: Path) -> list[dict]:
    lines = read_inbox(inbox).splitlines(keepends=True)
    tasks = []
    for index, line in enumerate(lines):
        match = TASK_LINE.match(line)
        if match:
            tasks.append({"index": index, "indent": len(match.group("indent").expandtabs(2)),
                          "done": match.group("done").lower() == "x", "text": match.group("text")})
    groups = []
    claimed: set[int] = set()
    for position, task in enumerate(tasks):
        if task["done"] or task["index"] in claimed:
            continue
        end = next((candidate["index"] for candidate in tasks[position + 1:]
                    if candidate["indent"] <= task["indent"]), len(lines))
        members = [candidate for candidate in tasks[position:]
                   if task["index"] <= candidate["index"] < end and not candidate["done"]]
        root_urls = list(HTTP_URL.finditer(task["text"]))
        root_label = re.sub(
            r"(?<![\w#])#[^\s#]*", "", HTTP_URL.sub("", task["text"])
        ).strip()
        if not root_urls and root_label.casefold() != "compose":
            members = [task]
        indices = [member["index"] for member in members]
        claimed.update(indices)
        urls = []
        raw_tags = []
        for member in members:
            urls.extend(match.group(0).rstrip(".,!?;:)]}") for match in HTTP_URL.finditer(member["text"]))
            raw_tags.extend(inbox_line_tags(member["text"]))
        if urls:
            entries = [(member["indent"], member["text"]) for member in members]
            groups.append({"indices": indices, "entries": entries,
                           "urls": dedupe_urls(urls), "tags": queue_tags(raw_tags)})
    return groups


def mark_completed_group(inbox: Path, group: dict) -> bool:
    try:
        original = read_inbox(inbox)
        lines = original.splitlines(keepends=True)
        current = []
        for index, line in enumerate(lines):
            match = TASK_LINE.match(line)
            if match and match.group("done").lower() != "x":
                current.append((index, len(match.group("indent").expandtabs(2)), match.group("text")))
        entries = group["entries"]
        match_start = next((start for start in range(len(current) - len(entries) + 1)
                            if [(item[1], item[2]) for item in current[start:start + len(entries)]] == entries), None)
        if match_start is None:
            return True
        for index, _, _ in current[match_start:match_start + len(entries)]:
            lines[index] = lines[index].replace("[ ]", "[x]", 1)
        updated = "".join(lines)
        if updated == original:
            return False
        temporary = inbox.with_suffix(inbox.suffix + ".tmp")
        temporary.write_text(updated)
        if read_inbox(inbox) != original:
            temporary.unlink(missing_ok=True)
            return True
        replace_text(temporary, inbox)
        return False
    except (OSError, ValueError) as error:
        stamp_print(f"error: cannot update {inbox}: {error}", error=True)
        return True


def inbox_group_signature(group: dict) -> tuple:
    structure = tuple(
        (indent, HTTP_URL.sub("", text).strip().casefold())
        for indent, text in group["entries"]
    )
    return structure, tuple(canonical_url(url) for url in group["urls"])


def mark_completed_signature(inbox: Path, signature: tuple) -> bool:
    while True:
        groups = [group for group in pending_inbox_groups(inbox)
                  if inbox_group_signature(group) == signature]
        if not groups:
            return False
        if mark_completed_group(inbox, groups[0]):
            return True


def mark_completed_lines(inbox: Path, indices: list[int]) -> bool:
    # Kept for callers of the old helper; new queue processing uses group identity.
    entries = []
    for index, line in enumerate(read_inbox(inbox).splitlines()):
        if index not in indices:
            continue
        match = TASK_LINE.match(line)
        if match:
            entries.append((len(match.group("indent").expandtabs(2)), match.group("text")))
    return mark_completed_group(inbox, {"entries": entries}) if entries else False


def mark_completed(inbox: Path, completed: set[str]) -> bool:
    failed = False
    for url in tuple(completed):
        try:
            original = read_inbox(inbox)
            lines = original.splitlines(keepends=True)
            updated = "".join(
                line.replace("[ ]", "[x]", 1)
                if (match := INBOX_TASK.match(line)) and match.group(1) == url else line
                for line in lines
            )
            if updated != original:
                temporary = inbox.with_suffix(inbox.suffix + ".tmp")
                temporary.write_text(updated)
                if read_inbox(inbox) != original:
                    temporary.unlink(missing_ok=True)
                    continue
                replace_text(temporary, inbox)
            completed.remove(url)
        except (OSError, ValueError) as error:
            stamp_print(f"error: cannot update {inbox}: {error}", error=True)
            failed = True
    return failed


def watch_inbox(
    inbox: Path,
    browser: str | None,
    notes: Path,
    attachments: Path,
    account: str | None = None,
    interval: float = INBOX_WATCH_INTERVAL,
    twitter_account: str | None = None,
    tags: list[str] | None = None,
) -> int:
    completed: set[str] = set()
    reported = load_report_state()
    if not inbox.exists():
        process_inbox(
            inbox, browser, notes, attachments, account, completed, False, twitter_account, tags,
            reported=reported,
        )
    previous: object = object()
    reported_error = None
    stamp_print(f"ok: watching {inbox}")
    try:
        while True:
            try:
                if not inbox.exists():
                    previous = None
                else:
                    current = read_inbox(inbox)
                    if current != previous or completed:
                        process_inbox(
                            inbox, browser, notes, attachments, account, completed, False,
                            twitter_account, tags, reported=reported,
                        )
                    previous = read_inbox(inbox)
                reported_error = None
                reported.pop("watch", None)
            except (OSError, ValueError) as error:
                if str(error) != reported_error:
                    stamp_print(f"error: {error}", error=True)
                    reported_error = str(error)
                report_queue_failure("Inbox", str(error), key="watch", reported=reported)
            save_report_state(reported)
            time.sleep(interval)
    except KeyboardInterrupt:
        stamp_print("ok: stopped watching inbox")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=("Save Instagram, Twitter/X, Reddit, and RedNote images as local Captures "
                     "or publish Telegram Deliveries."),
        epilog=("Forms: kakera --share telegram URL [URL ...]; "
                "kakera --compose --share telegram URL [URL ...]; "
                "kakera share telegram SELECTOR [SELECTOR ...]; "
                "kakera share telegram-only SELECTOR [SELECTOR ...]; "
                "kakera --share telegram-only URL [URL ...]; kakera telegram [--watch]. "
                "Use --tag share/telegram for the current Capture request."),
    )
    parser.add_argument("--version", action="version", version=f"Kakera {VERSION}")
    parser.add_argument("--browser", help="browser cookies to pass to gallery-dl, e.g. safari")
    parser.add_argument("--account", help="saved Instagram account alias")
    parser.add_argument(
        "--instagram-cookies", metavar="ACCOUNT", help="save the current Instagram session"
    )
    parser.add_argument(
        "--twitter-cookies", metavar="ACCOUNT", help="save the current Twitter/X session"
    )
    parser.add_argument("--twitter-account", help="saved Twitter account alias")
    parser.add_argument("--obsidian", action="store_true", help=f"use folders from {CONFIG.name}")
    parser.add_argument("--inbox", action="store_true", help="process unchecked links in the Obsidian inbox")
    parser.add_argument("--watch", action="store_true",
                        help="keep watching the inbox, Todoist, or Telegram Intake")
    parser.add_argument("--interval", metavar="DURATION",
                        help="watch poll interval, e.g. 30s, 3m, 1h")
    parser.add_argument("--todoist", action="store_true", help="process open Todoist tasks")
    parser.add_argument("--compose", action="store_true",
                        help="compose multiple URLs into one Source Note")
    parser.add_argument(
        "--share", choices=("telegram", "telegram-only"),
        help=("telegram: save the Capture first, then publish this request to Telegram; "
              "telegram-only: fetch and publish URLs with no durable Kakera output"),
    )
    parser.add_argument("--telegram-note", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--telegram-note-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--telegram-intake", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--local", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--tag", action="append", default=[], metavar="TAG",
                        help="add a Capture tag; may be repeated")
    parser.add_argument("--reddit-oauth", nargs="?", const="", metavar="USERNAME")
    parser.add_argument("urls", nargs="*", metavar="INPUT",
                        help="URL, or note selector for a note publishing command")
    arguments = parser.parse_args()

    pseudo_names = {"local", "inbox", "todoist", "telegram", "telegram-only", "share",
                    "instagram-cookies", "twitter-cookies", "reddit-oauth"}
    note_mode = arguments.telegram_note or arguments.telegram_note_only
    if not note_mode and any(url in pseudo_names for url in arguments.urls):
        parser.error("pseudo-subcommands must be the first command word")

    try:
        cli_tags = normalize_tags(arguments.tag)
    except ValueError as error:
        parser.error(str(error))
    if cli_tags and (arguments.instagram_cookies is not None or
                     arguments.twitter_cookies is not None or
                     arguments.reddit_oauth is not None):
        parser.error("--tag requires a capture command")
    if telegram_only_requested(cli_tags) and not arguments.inbox and not arguments.todoist:
        parser.error("--tag share/telegram-only requires --inbox or --todoist")
    cli_interval = None
    if arguments.interval is not None:
        try:
            cli_interval = parse_interval(arguments.interval)
        except ValueError as error:
            parser.error(f"interval {error}")
    auth_options = (
        arguments.instagram_cookies is not None, arguments.twitter_cookies is not None,
        arguments.reddit_oauth is not None,
    )
    share_telegram = arguments.share == "telegram"
    share_telegram_only = arguments.share == "telegram-only"
    if share_telegram and any(auth_options):
        parser.error("--share telegram cannot be combined with cookie export or Reddit OAuth")
    if (share_telegram_only and (share_telegram or arguments.compose or arguments.local or
                                 arguments.obsidian or arguments.inbox or arguments.todoist or
                                 arguments.watch or arguments.interval or arguments.tag or
                                 arguments.telegram_intake or any(auth_options))):
        parser.error("--share telegram-only accepts URLs and source account options only")
    if arguments.telegram_note or arguments.telegram_note_only:
        if not arguments.urls:
            parser.error("share telegram note publishing requires at least one note selector")
        if (arguments.browser or arguments.account or arguments.twitter_account or
                any(auth_options) or arguments.obsidian or arguments.inbox or arguments.watch or
                arguments.todoist or arguments.compose or arguments.share or
                arguments.telegram_intake or
                arguments.local or arguments.tag or arguments.interval):
            parser.error("share telegram note publishing accepts selectors only")
        try:
            return (telegram_note_only_command(arguments.urls) if arguments.telegram_note_only
                    else telegram_command(arguments.urls))
        except (OSError, ValueError) as error:
            parser.error(str(error))
    if share_telegram_only and not arguments.urls:
        parser.error("--share telegram-only requires at least one URL")
    if share_telegram_only:
        try:
            configured_telegram_chat_id()
            telegram_token()
            browser = arguments.browser or configured_browser()
            account = account_name(arguments.account) if arguments.account else (
                None if arguments.browser else configured_instagram_account()
            )
            twitter_account = account_name(arguments.twitter_account) if arguments.twitter_account else (
                None if arguments.browser else configured_twitter_account()
            )
        except (OSError, ValueError) as error:
            parser.error(str(error))
        failed = False
        for url in arguments.urls:
            try:
                success, message = telegram_only_url(url, browser, account, twitter_account)
            except (OSError, ValueError) as error:
                success, message = False, f"Telegram not sent: {error}"
            print(f"{'ok' if success else 'error'}: {url}: {message}")
            failed |= not success
        return int(failed)
    if share_telegram:
        cli_tags = merge_tags(cli_tags, [TELEGRAM_TAG])
    if arguments.interval is not None and not arguments.watch:
        parser.error("--interval requires --watch")
    if arguments.telegram_intake and arguments.interval is not None:
        parser.error("--interval cannot be used with telegram")

    if arguments.instagram_cookies is not None:
        if arguments.urls:
            parser.error("--instagram-cookies does not accept URLs")
        try:
            browser = arguments.browser or configured_browser() or "orion"
            return instagram_cookies(arguments.instagram_cookies, browser)
        except (OSError, ValueError) as error:
            parser.error(str(error))

    if arguments.twitter_cookies is not None:
        if arguments.urls:
            parser.error("--twitter-cookies does not accept URLs")
        try:
            browser = arguments.browser or configured_browser() or "orion"
            return twitter_cookies(arguments.twitter_cookies, browser)
        except (OSError, ValueError) as error:
            parser.error(str(error))

    if arguments.reddit_oauth is not None:
        if arguments.urls:
            parser.error("--reddit-oauth does not accept URLs")
        try:
            username = arguments.reddit_oauth or input("Reddit username: ")
            return reddit_oauth(username)
        except (EOFError, OSError, ValueError) as error:
            parser.error(str(error))

    if arguments.telegram_intake:
        if arguments.urls:
            parser.error("telegram does not accept URLs")
        if (arguments.share or arguments.compose or arguments.local or arguments.obsidian or
                arguments.inbox or arguments.todoist or arguments.tag or any(auth_options)):
            parser.error("telegram accepts --watch and source account options only")
        try:
            browser = arguments.browser or configured_browser()
            account = account_name(arguments.account) if arguments.account else (
                None if arguments.browser else configured_instagram_account()
            )
            twitter_account = account_name(arguments.twitter_account) if arguments.twitter_account else (
                None if arguments.browser else configured_twitter_account()
            )
            return telegram_intake(browser, account, twitter_account, watch=arguments.watch)
        except (OSError, ValueError) as error:
            parser.error(str(error))

    try:
        browser = arguments.browser or configured_browser()
        explicit_account = account_name(arguments.account) if arguments.account else None
        explicit_twitter_account = (
            account_name(arguments.twitter_account) if arguments.twitter_account else None
        )
        account = explicit_account or (
            None if arguments.browser else configured_instagram_account()
        )
        twitter_account = explicit_twitter_account or (
            None if arguments.browser else configured_twitter_account()
        )
        notes, attachments = configured_folders() if not arguments.local and (arguments.obsidian or arguments.inbox or arguments.todoist or share_telegram) else (
            ROOT / "downloads",
            ROOT / "attachments",
        )
    except ValueError as error:
        parser.error(str(error))

    if arguments.inbox:
        if arguments.todoist:
            parser.error("--inbox and --todoist cannot be combined")
        if arguments.compose:
            parser.error("--compose is automatic for inbox task groups")
        if arguments.urls:
            parser.error("--inbox does not accept URLs")
        try:
            if arguments.watch:
                return watch_inbox(
                    configured_inbox(), browser, notes, attachments, account,
                    cli_interval if cli_interval is not None else configured_interval("obsidian", INBOX_WATCH_INTERVAL),
                    twitter_account=twitter_account, tags=cli_tags,
                )
            return process_inbox(
                configured_inbox(), browser, notes, attachments, account,
                twitter_account=twitter_account, tags=cli_tags,
            )
        except (OSError, ValueError) as error:
            parser.error(str(error))
    if arguments.todoist:
        if arguments.urls:
            parser.error("--todoist does not accept URLs")
        if arguments.compose:
            parser.error("--compose is automatic for Todoist task groups")
        try:
            if arguments.watch:
                return watch_todoist(
                    browser, notes, attachments, account,
                    cli_interval if cli_interval is not None else configured_interval("todoist", TODOIST_WATCH_INTERVAL),
                    twitter_account=twitter_account, tags=cli_tags,
                )
            return process_todoist(
                browser, notes, attachments, account,
                twitter_account=twitter_account, tags=cli_tags,
            )
        except (OSError, ValueError) as error:
            parser.error(str(error))
    if arguments.watch:
        parser.error("--watch requires --inbox, --todoist, or telegram")
    if not arguments.urls:
        parser.error("provide at least one URL")

    if arguments.compose:
        if cli_tags:
            success, message = save_composed(arguments.urls, browser, notes, attachments,
                                              account, twitter_account, cli_tags)
        else:
            success, message = save_composed(arguments.urls, browser, notes, attachments,
                                              account, twitter_account)
        print(f"{process_stamp()} {'ok' if success else 'error'}: {', '.join(arguments.urls)}: {message}")
        return int(not success)
    failed = False
    for url in arguments.urls:
        if cli_tags:
            success, message = save(url, browser, notes, attachments, account, twitter_account, cli_tags)
        else:
            success, message = save(url, browser, notes, attachments, account, twitter_account)
        print(f"{process_stamp()} {'ok' if success else 'error'}: {url}: {message}")
        failed |= not success
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
