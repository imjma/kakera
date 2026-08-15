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
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "kakera.json"
COOKIE_DIR = ROOT / ".cookies"
VERSION = "0.1.0"
REDDIT_CLIENT_ID = "6N9uN0krSDE-ig"
TRACKING_PARAMETERS = {
    "igsh",
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
TODOIST_API = "https://api.todoist.com/api/v1"
SOURCE_SERVICES = {"instagram", "twitter", "reddit", "rednote"}


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
    parts = urlsplit(url)
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parts.query) if key not in TRACKING_PARAMETERS]
    )
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), query, ""))


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
        if len(segments) >= 2 and segments[0] in {"p", "reel", "tv"}:
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
        if host in {"xhslink.com", "www.xhslink.com"} and len(segments) >= 2 and segments[0] == "o":
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


def process_todoist(
    browser: str | None,
    notes: Path,
    attachments: Path,
    account: str | None = None,
    twitter_account: str | None = None,
    tags: list[str] | None = None,
) -> int:
    token = todoist_token()
    project_id = configured_todoist_project()
    failed = False
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
            print(f"ok: skipped Todoist task {task.get('id', '?')}: no HTTP(S) URL")
            continue
        item_tags = queue_tags(list(tree_tags(task))) if hierarchy else queue_tags(task_tags(task))
        capture_tags = merge_tags(item_tags, tags or [])
        if len(urls) > 1:
            success, message = (
                save_composed(urls, browser, notes, attachments, account, twitter_account,
                              capture_tags)
                if capture_tags else
                save_composed(urls, browser, notes, attachments, account, twitter_account)
            )
        else:
            success, message = (
                save(urls[0], browser, notes, attachments, account, twitter_account,
                     capture_tags)
                if capture_tags else
                save(urls[0], browser, notes, attachments, account, twitter_account)
            )
        print(f"{'ok' if success else 'error'}: {', '.join(urls)}: {message}")
        if not success:
            failed = True
            continue
        try:
            todoist_request(token, f"/tasks/{quote(str(task.get('id', '')), safe='')}/close", "POST")
        except ValueError as error:
            print(f"error: Todoist task {task.get('id', '?')} was saved but not closed: {error}", file=sys.stderr)
            failed = True
    return int(failed)


def watch_todoist(
    browser: str | None,
    notes: Path,
    attachments: Path,
    account: str | None = None,
    interval: float = 30,
    twitter_account: str | None = None,
    tags: list[str] | None = None,
) -> int:
    print("ok: watching Todoist")
    try:
        while True:
            try:
                process_todoist(browser, notes, attachments, account, twitter_account, tags)
            except (OSError, ValueError) as error:
                print(f"error: {error}", file=sys.stderr)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("ok: stopped watching Todoist")
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
    if data[4:8] == b"ftyp" and data[8:12] in {b"avif", b"avis"}:
        return ".avif"
    if data[4:8] == b"ftyp" and data[8:12] in {b"heic", b"heix", b"hevc", b"hevx", b"mif1"}:
        return ".heic"
    return None


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


def parse_rednote(page: str, source: str) -> tuple[str, dict, list[str]]:
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
    if not images:
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
    return f"rednote-{note_id}", metadata, images


def download_rednote(url: str, directory: Path) -> tuple[str, dict]:
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) Kakera/0.1"}
    try:
        with urlopen(Request(url, headers=headers), timeout=30) as response:
            page = response.read(5 * 1024 * 1024 + 1)
            final_url = response.url
        if len(page) > 5 * 1024 * 1024:
            raise ValueError("RedNote page is unexpectedly large")
        name, metadata, images = parse_rednote(page.decode("utf-8"), url)
        for index, image_url in enumerate(images, 1):
            request = Request(image_url, headers={**headers, "Referer": final_url})
            with urlopen(request, timeout=30) as response:
                data = response.read(50 * 1024 * 1024 + 1)
            if len(data) > 50 * 1024 * 1024:
                raise ValueError("RedNote image exceeds 50 MB")
            (directory / f"{index:02}.image").write_bytes(data)
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
    return metadata


def _source_fields(source: dict) -> list[tuple[str, str]]:
    metadata, service, url = source["metadata"], source["service"], source["url"]
    fields = [("URL", metadata.get("post_url") or url), ("Title", note_title(metadata, service))]
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
    properties.append(f"{link_property}: {json.dumps(primary['url'], ensure_ascii=False)}")
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
    properties.append("---")

    body = [_source_description(metadata, service)]
    if primary_ok:
        primary_body = [_source_image_links(primary["images"], note)]
        if primary.get("error"):
            primary_body.append(f"Warning: {primary['error']}")
        body.append("\n\n".join(primary_body))
    else:
        label = "Unknown" if primary["service"] == "unknown" else "Failed"
        body.append(f"## Source 1 — {label}\n- URL: {primary['url']}\n- Failure: {primary['error']}")
    for index, source in enumerate(sources[1:], 2):
        heading = source["service"].title() if source["service"] != "rednote" else "小红书"
        section = [f"## Source {index} — {heading}"]
        if source.get("error") and not source.get("images"):
            section.extend((f"- URL: {source['url']}", f"- Failure: {source['error']}"))
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
        except OSError as error:
            return False, f"cannot save capture: {error}"
        return True, f"saved {len(stored)} image(s)"


def fetch_source(
    url: str,
    browser: str | None,
    account: str | None,
    twitter_account: str | None,
    directory: Path,
    name: str | None = None,
) -> tuple[dict | None, str | None]:
    directory.mkdir(parents=True, exist_ok=True)
    try:
        name = name or capture_id(url)
    except ValueError as error:
        return None, str(error)
    metadata: dict = {}
    result = None
    if name.startswith("rednote-"):
        try:
            name, metadata = download_rednote(url, directory)
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
        if name.startswith("twitter-"):
            command.extend(("-o", "extractor.twitter.videos=false"))
        command.append(url)
        result = subprocess.run(command, capture_output=True, text=True)
    if result and result.returncode:
        error = next((line for line in reversed(result.stderr.splitlines()) if line.strip()), "download failed")
        if "[instagram]" in error and "login page" in error:
            if account:
                error = (f"Instagram rejected saved account {account!r}; switch to it in "
                         f"Orion and run kakera instagram-cookies {account} again")
            elif browser:
                error = f"Instagram rejected the {browser} browser session; sign in there and retry"
            else:
                error = "Instagram requires a logged-in browser session; retry with --browser safari"
        elif "[reddit]" in error:
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
    valid = [(path, image_extension(path)) for path in candidates]
    valid = [(path, extension) for path, extension in valid if extension]
    if not valid:
        return None, "no supported images found"
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
    metadata = _normalise_source_metadata(name, service, metadata, url)
    return {"name": name, "service": service, "url": url, "metadata": metadata, "valid": valid}, None


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
) -> tuple[bool, str]:
    unique = dedupe_urls(urls)
    if not unique:
        return False, "provide at least one URL"
    if len(unique) == 1:
        if tags:
            return save(unique[0], browser, notes, attachment_root, account, twitter_account, tags)
        return save(unique[0], browser, notes, attachment_root, account, twitter_account)
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
        successful = [source for source in sources if source.get("valid")]
        if not successful:
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
        except OSError as error:
            return False, f"cannot save composition: {error}"
        failures = sum(1 for source in sources if source.get("error"))
        suffix = f"; {failures} source(s) failed" if failures else ""
        return True, f"saved {sum(len(source['images']) for source in successful)} image(s){suffix}"


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
) -> int:
    if not inbox.exists():
        inbox.parent.mkdir(parents=True, exist_ok=True)
        inbox.write_text("# Kakera Inbox\n\n")
        print(f"ok: created {inbox}")
        return 0

    completed = completed if completed is not None else set()
    failed = False
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
            else:
                failed = True
            continue
        urls = group["urls"]
        capture_tags = merge_tags(group.get("tags", []), tags or [])
        if len(urls) > 1:
            success, message = (
                save_composed(urls, browser, notes, attachments, account, twitter_account,
                              capture_tags)
                if capture_tags else
                save_composed(urls, browser, notes, attachments, account, twitter_account)
            )
        else:
            success, message = (
                save(urls[0], browser, notes, attachments, account, twitter_account,
                     capture_tags)
                if capture_tags else
                save(urls[0], browser, notes, attachments, account, twitter_account)
            )
        print(f"{'ok' if success else 'error'}: {', '.join(urls)}: {message}")
        if success:
            completion_failed = mark_completed_signature(inbox, signature)
            failed |= completion_failed
            if completion_failed:
                completed.add(signature)
        failed |= not success
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
        print(f"error: cannot update {inbox}: {error}", file=sys.stderr)
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
            print(f"error: cannot update {inbox}: {error}", file=sys.stderr)
            failed = True
    return failed


def watch_inbox(
    inbox: Path,
    browser: str | None,
    notes: Path,
    attachments: Path,
    account: str | None = None,
    interval: float = 2,
    twitter_account: str | None = None,
    tags: list[str] | None = None,
) -> int:
    completed: set[str] = set()
    if not inbox.exists():
        process_inbox(inbox, browser, notes, attachments, account, completed, False, twitter_account, tags)
    previous: object = object()
    reported_error = None
    print(f"ok: watching {inbox}")
    try:
        while True:
            try:
                if not inbox.exists():
                    previous = None
                else:
                    current = read_inbox(inbox)
                    if current != previous:
                        process_inbox(inbox, browser, notes, attachments, account, completed, False, twitter_account, tags)
                    elif completed:
                        process_inbox(inbox, browser, notes, attachments, account, completed, False, twitter_account, tags)
                    previous = read_inbox(inbox)
                reported_error = None
            except (OSError, ValueError) as error:
                if str(error) != reported_error:
                    print(f"error: {error}", file=sys.stderr)
                    reported_error = str(error)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("ok: stopped watching inbox")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Save Instagram, Twitter/X, Reddit, and RedNote images as Markdown captures.",
        epilog=("Shortcuts: kakera URL, kakera local URL, kakera inbox, "
                "kakera todoist, kakera instagram-cookies, kakera twitter-cookies, "
                "kakera reddit-oauth"),
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
    parser.add_argument("--watch", action="store_true", help="keep watching the inbox or Todoist")
    parser.add_argument("--todoist", action="store_true", help="process open Todoist tasks")
    parser.add_argument("--compose", action="store_true",
                        help="compose multiple URLs into one Source Note")
    parser.add_argument("--tag", action="append", default=[], metavar="TAG",
                        help="add a Capture tag; may be repeated")
    parser.add_argument("--reddit-oauth", nargs="?", const="", metavar="USERNAME")
    parser.add_argument("urls", nargs="*")
    arguments = parser.parse_args()

    try:
        cli_tags = normalize_tags(arguments.tag)
    except ValueError as error:
        parser.error(str(error))
    if cli_tags and (arguments.instagram_cookies is not None or
                     arguments.twitter_cookies is not None or
                     arguments.reddit_oauth is not None):
        parser.error("--tag requires a capture command")

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
        notes, attachments = configured_folders() if arguments.obsidian or arguments.inbox or arguments.todoist else (
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
            function = watch_inbox if arguments.watch else process_inbox
            return function(
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
            function = watch_todoist if arguments.watch else process_todoist
            return function(
                browser, notes, attachments, account,
                twitter_account=twitter_account, tags=cli_tags,
            )
        except (OSError, ValueError) as error:
            parser.error(str(error))
    if arguments.watch:
        parser.error("--watch requires --inbox or --todoist")
    if not arguments.urls:
        parser.error("provide at least one URL")

    if arguments.compose:
        if cli_tags:
            success, message = save_composed(arguments.urls, browser, notes, attachments,
                                              account, twitter_account, cli_tags)
        else:
            success, message = save_composed(arguments.urls, browser, notes, attachments,
                                              account, twitter_account)
        print(f"{'ok' if success else 'error'}: {', '.join(arguments.urls)}: {message}")
        return int(not success)
    failed = False
    for url in arguments.urls:
        if cli_tags:
            success, message = save(url, browser, notes, attachments, account, twitter_account, cli_tags)
        else:
            success, message = save(url, browser, notes, attachments, account, twitter_account)
        print(f"{'ok' if success else 'error'}: {url}: {message}")
        failed |= not success
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
