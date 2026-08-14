#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["gallery-dl>=1.32.9,<2"]
# ///

import argparse
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
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


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parts.query) if key not in TRACKING_PARAMETERS]
    )
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), query, ""))


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
    elif host in {"reddit.com", "www.reddit.com", "old.reddit.com", "np.reddit.com", "redd.it"}:
        service = "reddit"
        if host == "redd.it" and segments:
            post_id = segments[0]
        else:
            for marker in ("comments", "gallery"):
                if marker in segments and segments.index(marker) + 1 < len(segments):
                    post_id = segments[segments.index(marker) + 1]
                    break
    elif host in rednote_hosts:
        service = "rednote"
        if host.endswith("xiaohongshu.com"):
            for marker in ("explore", "item"):
                if marker in segments and segments.index(marker) + 1 < len(segments):
                    post_id = segments[segments.index(marker) + 1]
                    break
    else:
        raise ValueError("supported sources are Instagram, Reddit, and RedNote")

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


def instagram_cookie_path(account: str) -> Path:
    return COOKIE_DIR / f"instagram-{account_name(account)}.txt"


def instagram_cookies(account: str, browser: str = "orion") -> int:
    destination = instagram_cookie_path(account)
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
    authenticated = False
    for line in lines:
        fields = line.removeprefix("#HttpOnly_").split("\t")
        if len(fields) != 7 or not (
            fields[0] == "instagram.com" or fields[0].endswith(".instagram.com")
        ):
            continue
        kept.append(line)
        authenticated |= fields[5] == "sessionid" and bool(fields[6])
    if not authenticated:
        print("error: Orion has no logged-in Instagram session", file=sys.stderr)
        return 1

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.chmod(0o700)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text("# Netscape HTTP Cookie File\n\n" + "\n".join(kept) + "\n")
    temporary.chmod(0o600)
    temporary.replace(destination)
    print(f"ok: saved Instagram account {account!r}")
    return 0


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


def parse_rednote(page: str, source: str) -> tuple[str, dict, list[str]]:
    match = re.search(r"window\.__INITIAL_STATE__=(.*?)</script>", page, re.S)
    if not match:
        raise ValueError("RedNote page did not include public note data")
    try:
        state = json.loads(re.sub(r"(?<=:)undefined(?=[,}])", "null", match.group(1)))
        note = next(iter(state["note"]["noteDetailMap"].values()))["note"]
        note_id = note["noteId"]
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
    description = metadata.get("description") or metadata.get("selftext") or ""
    return metadata.get("title") or next(
        (line.strip() for line in description.splitlines() if line.strip()),
        f"{service.title()} capture",
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


def note_path(notes: Path, name: str, metadata: dict) -> Path:
    service, post_id = name.split("-", 1)
    for path in notes.glob("*.md"):
        try:
            if f"{name}-" in path.read_text():
                return path
        except OSError:
            pass
    filename = note_filename(metadata, service)
    candidate = notes / f"{filename or name}.md"
    if candidate.exists():
        filename = note_filename(metadata, service, post_id)
        candidate = notes / f"{filename or name}.md"
    return candidate


def write_note(
    note: Path,
    source: str,
    images: list[Path],
    metadata: dict | None = None,
    service: str | None = None,
) -> None:
    metadata = metadata or {}
    service = service or note.stem.split("-", 1)[0]
    description = metadata.get("description") or metadata.get("selftext") or ""
    title = note_title(metadata, service)
    username = metadata.get("username") or metadata.get("author")
    author = metadata.get("fullname") or username
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
        author_url = (
            f"https://www.instagram.com/{username}/"
            if service == "instagram"
            else f"https://www.reddit.com/user/{username}/"
        )
        properties.append(f"author_url: {json.dumps(author_url, ensure_ascii=False)}")
    elif metadata.get("author_url"):
        properties.append(f"author_url: {json.dumps(metadata['author_url'], ensure_ascii=False)}")
    if published:
        properties.append(f"published: {json.dumps(str(published), ensure_ascii=False)}")
    properties.extend(("tags:", f"  - {service}", "---"))

    links = "\n".join(
        f"![]({Path(os.path.relpath(image, note.parent)).as_posix()})" for image in images
    )
    content = "\n".join(properties) + f"\n\n{description.strip()}\n\n{links}\n"
    temporary = note.with_suffix(".tmp")
    temporary.write_text(content)
    replace_text(temporary, note)


def save(
    url: str,
    browser: str | None,
    notes: Path = ROOT / "downloads",
    attachment_root: Path = ROOT / "attachments",
    account: str | None = None,
) -> tuple[bool, str]:
    try:
        name = capture_id(url)
    except ValueError as error:
        return False, str(error)

    with tempfile.TemporaryDirectory(prefix="kakera-") as directory:
        if name.startswith("rednote-"):
            try:
                name, metadata = download_rednote(url, Path(directory))
            except ValueError as error:
                return False, str(error)
            result = None
        else:
            metadata = {}
        command = [
            sys.executable,
            "-m",
            "gallery_dl",
            "--config-ignore",
            "--no-input",
            "--filesize-max",
            "50M",
            "--directory",
            directory,
            "--filename",
            "/O",
            "--write-metadata",
        ]
        if not name.startswith("rednote-"):
            if name.startswith("instagram-") and account:
                cookies = instagram_cookie_path(account)
                if not cookies.is_file():
                    return False, f"Instagram account {account!r} has no saved cookies"
                command.extend(("--cookies", str(cookies)))
            elif browser:
                command.extend(("--cookies-from-browser", browser))
            if name.startswith("reddit-"):
                try:
                    reddit = configured_reddit()
                except ValueError as error:
                    return False, str(error)
                if reddit:
                    command.extend((
                        "-o", f"extractor.reddit.client-id={reddit[0]}",
                        "-o", f"extractor.reddit.user-agent={reddit[1]}",
                    ))
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
            elif "[reddit]" in error and not configured_reddit():
                error += f"; add reddit.client_id and reddit.user_agent to {CONFIG.name}"
            return False, error

        candidates = sorted(path for path in Path(directory).rglob("*") if path.is_file())
        if not metadata:
            for path in candidates:
                if path.suffix == ".json":
                    try:
                        metadata = json.loads(path.read_text())
                        break
                    except (OSError, json.JSONDecodeError):
                        pass
        valid = [(path, image_extension(path)) for path in candidates]
        valid = [(path, extension) for path, extension in valid if extension]
        if not valid:
            return False, "no supported images found"

        service = name.split("-", 1)[0]
        attachments = attachment_root / service
        try:
            notes.mkdir(parents=True, exist_ok=True)
            attachments.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            return False, f"cannot create output folders: {error}"
        existing = sorted(path for path in attachments.glob(f"{name}-*.*") if image_extension(path))
        hashes = {digest(path) for path in existing}
        numbers = [
            int(match.group(1))
            for path in existing
            if (match := re.fullmatch(rf"{re.escape(name)}-(\d+)\.[^.]+", path.name))
        ]
        next_number = max(numbers, default=0) + 1

        for source, extension in valid:
            source_hash = digest(source)
            if source_hash in hashes:
                continue
            target = attachments / f"{name}-{next_number:02}{extension}"
            with source.open("rb") as incoming, target.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            hashes.add(source_hash)
            existing.append(target)
            next_number += 1

        write_note(note_path(notes, name, metadata), url, sorted(existing), metadata, service)
        return True, f"saved {len(existing)} image(s)"


def process_inbox(
    inbox: Path,
    browser: str | None,
    notes: Path,
    attachments: Path,
    account: str | None = None,
) -> int:
    if not inbox.exists():
        inbox.parent.mkdir(parents=True, exist_ok=True)
        inbox.write_text("# Kakera Inbox\n\n")
        print(f"ok: created {inbox}")
        return 0

    try:
        lines = inbox.read_text().splitlines(keepends=True)
    except OSError as error:
        if error.errno == errno.EPERM:
            raise ValueError(
                f"macOS blocked access to {inbox}; this iCloud File Provider path "
                "is not available to Kakera. Use a local vault or a normal iCloud "
                "Drive path visible in Finder"
            ) from error
        raise
    pending = [
        (index, match.group(1))
        for index, line in enumerate(lines)
        if (match := re.match(r"^\s*-\s+\[ \]\s+(https?://\S+)", line))
    ]
    if not pending:
        print("ok: no pending links")
        return 0

    failed = False
    for index, url in pending:
        success, message = save(url, browser, notes, attachments, account)
        print(f"{'ok' if success else 'error'}: {url}: {message}")
        if success:
            lines[index] = lines[index].replace("[ ]", "[x]", 1)
            temporary = inbox.with_suffix(inbox.suffix + ".tmp")
            temporary.write_text("".join(lines))
            replace_text(temporary, inbox)
        failed |= not success
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Save Instagram, Reddit, and RedNote images as Markdown captures.",
        epilog=("Shortcuts: kakera URL, kakera local URL, kakera inbox, "
                "kakera instagram-cookies, kakera reddit-oauth"),
    )
    parser.add_argument("--version", action="version", version=f"Kakera {VERSION}")
    parser.add_argument("--browser", help="browser cookies to pass to gallery-dl, e.g. safari")
    parser.add_argument("--account", help="saved Instagram account alias")
    parser.add_argument(
        "--instagram-cookies", metavar="ACCOUNT", help="save Orion's current Instagram session"
    )
    parser.add_argument("--obsidian", action="store_true", help=f"use folders from {CONFIG.name}")
    parser.add_argument("--inbox", action="store_true", help="process unchecked links in the Obsidian inbox")
    parser.add_argument("--reddit-oauth", nargs="?", const="", metavar="USERNAME")
    parser.add_argument("urls", nargs="*")
    arguments = parser.parse_args()

    if arguments.instagram_cookies is not None:
        if arguments.urls:
            parser.error("--instagram-cookies does not accept URLs")
        try:
            return instagram_cookies(arguments.instagram_cookies, arguments.browser or "orion")
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
        account = arguments.account or configured_instagram_account()
        notes, attachments = configured_folders() if arguments.obsidian or arguments.inbox else (
            ROOT / "downloads",
            ROOT / "attachments",
        )
    except ValueError as error:
        parser.error(str(error))

    if arguments.inbox:
        if arguments.urls:
            parser.error("--inbox does not accept URLs")
        try:
            return process_inbox(configured_inbox(), browser, notes, attachments, account)
        except (OSError, ValueError) as error:
            parser.error(str(error))
    if not arguments.urls:
        parser.error("provide at least one URL")

    failed = False
    for url in arguments.urls:
        success, message = save(url, browser, notes, attachments, account)
        print(f"{'ok' if success else 'error'}: {url}: {message}")
        failed |= not success
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
