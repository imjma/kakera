import errno
import hashlib
import io
import json
import multiprocessing as mp
import os
import re
import subprocess
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from threading import Barrier, Event, Lock, Thread
from pathlib import Path
from subprocess import CompletedProcess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import kakera
from kakera import (
    canonical_url,
    capture_id,
    configured_browser,
    configured_folders,
    configured_inbox,
    configured_instagram_account,
    configured_twitter_account,
    configured_reddit,
    instagram_cookies,
    twitter_cookies,
    main,
    replace_text,
    parse_rednote,
    process_inbox,
    process_todoist,
    reddit_oauth,
    watch_inbox,
    write_note,
)


def _process_store_worker(incoming, attachment_root, extension, queue, name="instagram-MP"):
    source = {
        "name": name,
        "service": "instagram",
        "valid": [(Path(incoming), extension)],
    }
    try:
        images = kakera.store_source_images(source, Path(attachment_root) / "notes", Path(attachment_root))
        queue.put(("ok", [str(path) for path in images], source.get("error")))
    except Exception as error:
        queue.put(("error", repr(error), None))


assert capture_id("https://www.instagram.com/p/ABC_123/?igsh=tracking") == "instagram-ABC_123"
for shape in ("p", "reel", "tv"):
    indexed = f"https://www.instagram.com/{shape}/DbqJEsLna8A/?foo=bar&igsh=tracking#comments"
    base = f"https://www.instagram.com/{shape}/DbqJEsLna8A/"
    assert kakera.normalize_instagram_post_url(indexed) == base
    assert canonical_url(indexed) == base.rstrip("/")
assert canonical_url("https://x.com/a/status/1?keep=yes#fragment") == "https://x.com/a/status/1?keep=yes"
assert kakera.dedupe_urls([
    "https://www.instagram.com/p/DbqJEsLna8A/?foo=bar",
    "https://www.instagram.com/p/DbqJEsLna8A/",
]) == ["https://www.instagram.com/p/DbqJEsLna8A/?foo=bar"]
with TemporaryDirectory() as directory:
    normalized_note_root = Path(directory)
    normalized_note = normalized_note_root / "existing.md"
    normalized_note.write_text('---\ninstagram: "https://www.instagram.com/p/DbqJEsLna8A/"\n---\n')
    assert kakera.note_path(
        normalized_note_root, "instagram-DbqJEsLna8A", {},
        "https://www.instagram.com/p/DbqJEsLna8A/?foo=bar#comments",
    ) == normalized_note
assert capture_id("https://redd.it/1abcxyz?utm_source=share") == "reddit-1abcxyz"
assert canonical_url("https://redd.it/1abcxyz/?utm_source=x&keep=yes#top") == "https://redd.it/1abcxyz?keep=yes"
assert capture_id("http://xhslink.com/o/ABC").startswith("rednote-")
assert capture_id("http://xhslink.com/m/1JXFZx4ards") == "rednote-1JXFZx4ards"
assert capture_id("https://x.com/artist/status/123456789") == "twitter-123456789"
assert capture_id("https://www.twitter.com/artist/status/123456789/?s=20") == "twitter-123456789"
assert capture_id("https://mobile.x.com/i/web/status/123456789") == "twitter-123456789"
assert capture_id("https://x.com/artist/status/123456789/photo/1") == "twitter-123456789"
assert capture_id("https://mobile.twitter.com/i/web/status/123456789/video/2") == "twitter-123456789"
assert kakera.normalize_tag(" #To Read ") == "To-Read"
assert kakera.normalize_tag("ｒｅｆｅｒｅｎｃｅ") == "reference"
assert kakera.normalize_tag("topic/📚") == "topic/📚"
assert kakera.normalize_tag("👩‍💻") == "👩‍💻"
assert kakera.normalize_tag("🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f") == "🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f"
assert kakera.normalize_tag("2024/year") == "2024/year"
assert kakera.normalize_tag("year/2024") == "year/2024"
try:
    kakera.normalize_tag("2024")
except ValueError:
    pass
else:
    raise AssertionError("accepted numeric-only tag")
assert kakera.merge_tags(["Twitter", "manual"], ["twitter", "extra"]) == ["Twitter", "manual", "extra"]
assert kakera.task_tags({"labels": ["Read Later", "topic"]}) == ["Read Later", "topic"]
assert kakera.inbox_line_tags(
    "#research, #later. #nested/tag; https://example.test/post#url-fragment"
) == ["research", "later", "nested/tag"]
assert kakera.inbox_line_tags(
    "#topic/📚 #year/2024 #one… #two— #three> #four| #five`"
) == ["topic/📚", "year/2024", "one", "two", "three", "four", "five"]
assert kakera.inbox_line_tags(
    "#🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f"
) == ["🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f"]
for invalid_tag in ("", "123", "bad.tag", "#bad#tag", "/nested"):
    try:
        kakera.normalize_tag(invalid_tag)
    except ValueError:
        pass
    else:
        raise AssertionError(f"accepted invalid tag: {invalid_tag!r}")
for invalid_sequence in ("\u200d", "\ufe0f", "\u0301", "🏴\U000e0067"):
    try:
        kakera.normalize_tag(invalid_sequence)
    except ValueError:
        pass
    else:
        raise AssertionError(f"accepted invalid Unicode tag sequence: {invalid_sequence!r}")
for invalid_twitter_url in (
    "https://x.com/artist",
    "https://x.com/artist/status/not-a-number",
    "https://x.com/search?q=python",
    "https://x.com/foo/bar/status/123",
    "https://x.com/i/web/status/123/likes",
    "https://x.com/artist/status/123/photo",
):
    try:
        capture_id(invalid_twitter_url)
    except ValueError:
        pass
    else:
        raise AssertionError(f"accepted non-status Twitter URL: {invalid_twitter_url}")

with TemporaryDirectory() as directory:
    root = Path(directory)
    temporary, destination = root / "inbox.tmp", root / "inbox.md"
    temporary.write_text("updated")
    destination.write_text("old")
    with patch.object(Path, "replace", side_effect=OSError(errno.EPERM, "provider")):
        replace_text(temporary, destination)
    assert destination.read_text() == "updated"

rednote_page = """<script>window.__INITIAL_STATE__={"global":{"value":undefined},"note":{"noteDetailMap":{"id":{"note":{"noteId":"abc123","title":"A note","desc":"Caption","time":1784807339000,"user":{"userId":"user1","nickname":"Artist"},"imageList":[{"urlDefault":"http://example.com/image"}]}}}}}</script>"""
rednote_name, rednote_metadata, rednote_images = parse_rednote(rednote_page, "http://xhslink.com/o/ABC")
assert rednote_name == "rednote-abc123"
assert rednote_metadata["author"] == "Artist"
assert rednote_metadata["post_url"] == "http://xhslink.com/o/ABC"
assert rednote_images == ["https://example.com/image"]

unsafe_rednote_page = rednote_page.replace('"noteId":"abc123"', '"noteId":"../escape"')
unsafe_name, _, _ = parse_rednote(unsafe_rednote_page, "http://xhslink.com/o/ABC")
unsafe_name_again, _, _ = parse_rednote(unsafe_rednote_page, "http://xhslink.com/o/ABC")
assert unsafe_name == unsafe_name_again and re.fullmatch(r"rednote-[A-Za-z0-9_-]{1,100}", unsafe_name)

with TemporaryDirectory() as directory:
    root = Path(directory)
    note = root / "downloads" / "instagram-ABC_123.md"
    image = root / "attachments" / "instagram" / "instagram-ABC_123-01.jpg"
    note.parent.mkdir()
    write_note(
        note,
        "https://www.instagram.com/p/ABC_123/",
        [image],
        {
            "description": "A caption\nwith body text",
            "username": "artist",
            "fullname": "The Artist",
            "post_date": "2026-08-01 09:12:15",
        },
    )
    text = note.read_text()
    assert 'title: "A caption - Instagram"' in text
    assert 'instagram: "https://www.instagram.com/p/ABC_123/"' in text
    assert 'author: "The Artist"' in text
    assert "published: \"2026-08-01 09:12:15\"" in text
    assert 'tags:\n  - "instagram"' in text
    assert "A caption\nwith body text" in text
    assert "![](../attachments/instagram/instagram-ABC_123-01.jpg)" in text

with TemporaryDirectory() as directory:
    root = Path(directory)
    inbox = root / "inbox.md"
    inbox.write_text(
        "- [ ] #Primary https://x.com/a/status/1#fragment\n"
        "  - [ ] #Nested/Tag https://x.com/a/status/2 #primary\n"
    )
    groups = kakera.pending_inbox_groups(inbox)
    assert groups[0]["tags"] == ["Primary", "Nested/Tag"]
    compose_inbox = root / "compose-inbox.md"
    compose_inbox.write_text(
        "- [ ] Compose #Parent\n"
        "  - [ ] #Child https://x.com/a/status/3\n"
    )
    compose_groups = kakera.pending_inbox_groups(compose_inbox)
    assert compose_groups[0]["tags"] == ["Parent", "Child"]

with TemporaryDirectory() as directory:
    root = Path(directory)
    notes, attachments = root / "notes", root / "attachments"

    def fake_tag_download(command, **_):
        target = Path(command[command.index("--directory") + 1]) / "tagged.jpg"
        target.write_bytes(b"\xff\xd8\xff\xe0tagged")
        target.with_suffix(".jpg.json").write_text(json.dumps({"description": "Tagged"}))
        return CompletedProcess(command, 0, "", "")

    with patch.object(kakera.subprocess, "run", side_effect=fake_tag_download):
        assert kakera.save(
            "https://www.instagram.com/p/TAGGED/", None, notes, attachments,
            tags=["Queue Tag", "Manual"],
        )[0]
        note = notes / "Tagged - Instagram.md"
        note.write_text(note.read_text().replace(
            '  - "Manual"', '  - "Manual"\n  - "Old"\n  - "bad.tag"'
        ))
        assert kakera.save(
            "https://www.instagram.com/p/TAGGED/", None, notes, attachments,
            tags=["queue-tag", "New"],
        )[0]
    text = note.read_text()
    assert '  - "instagram"' in text
    assert '  - "Manual"' in text and '  - "Old"' in text and '  - "New"' in text
    assert '  - "bad.tag"' not in text
    assert text.count('  - "Queue-Tag"') == 1

with TemporaryDirectory() as directory:
    root = Path(directory)
    forms = (
        ('tags: [manual, "Other"]', ["manual", "Other"]),
        ('tags: ["manual", \'Other\']', ["manual", "Other"]),
        ('tags: manual', ["manual"]),
        ('tags:\n    - "manual"\n      - Other', ["manual", "Other"]),
        ('tags:\n  - "manual"\n  - "Other"', ["manual", "Other"]),
    )
    for index, (raw_tags, expected) in enumerate(forms):
        note = root / f"note-{index}.md"
        note.write_text(f"---\ntitle: test\n{raw_tags}\n---\n")
        assert kakera.note_tags(note) == expected
    invalid = root / "invalid.md"
    invalid.write_text('---\ntags:\n  - "manual"\n  - "bad.tag"\n---\n')
    assert kakera.note_tags(invalid) == ["manual"]
    commented = root / "commented.md"
    commented.write_text(
        '---\n'
        'title: "foo---bar"\n'
        'instagram: "https://instagram.com/p/ID"\n'
        'author: "person #name" # trailing comment\n'
        'tags: [manual, "quoted#tag", Other] # list comment\n'
        '---\n'
    )
    assert kakera.note_tags(commented) == ["manual", "Other"]
    assert kakera.note_path(
        root, "instagram-ID", {"post_url": "https://instagram.com/p/ID"},
        "https://instagram.com/p/ID",
    ) == commented
    summary = root / "summary.md"
    summary.write_text(
        '---\n'
        'summary: |\n'
        '  ---\n'
        '  tags:\n'
        '    - ignored\n'
        'tags:\n'
        '  - manual\n'
        '---\n'
    )
    assert kakera.note_tags(summary) == ["manual"]
    nested = root / "nested-tags.md"
    nested.write_text(
        '---\n'
        'metadata:\n'
        '  tags:\n'
        '    - ignored\n'
        'tags:\n'
        '  - real\n'
        '---\n'
    )
    assert kakera.note_tags(nested) == ["real"]
    no_top_level = root / "no-top-level-tags.md"
    no_top_level.write_text(
        '---\n'
        'summary: |\n'
        '  tags:\n'
        '    - ignored\n'
        'metadata:\n'
        '  tags:\n'
        '    - also-ignored\n'
        '---\n'
    )
    assert kakera.note_tags(no_top_level) == []
    block_comments = root / "block-comments.md"
    block_comments.write_text(
        '---\n'
        'title: test\n'
        'tags:\n'
        '  # comment-only\n'
        '  - manual # item comment\n'
        '  # another comment\n'
        '  - "quoted#tag"\n'
        '---\n'
    )
    assert kakera.note_tags(block_comments) == ["manual"]

with patch.object(kakera.sys, "argv", ["kakera.py", "--tag", "bad.tag", "https://x.com/a/status/1"]):
    with patch.object(kakera, "save") as save:
        try:
            kakera.main()
        except SystemExit as error:
            assert error.code == 2
        else:
            raise AssertionError("invalid CLI tag did not abort")
        assert not save.called

with patch.object(kakera.sys, "argv", [
    "kakera.py", "--tag", "topic/📚", "--tag", "Manual", "one", "two",
]), patch.object(kakera, "save", return_value=(True, "saved")) as save:
    assert kakera.main() == 0
    assert [call.args[-1] for call in save.call_args_list] == [["topic/📚", "Manual"]] * 2

with patch.object(kakera.sys, "argv", [
    "kakera.py", "--tag", "🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f", "one",
]), patch.object(kakera, "save", return_value=(True, "saved")) as save:
    assert kakera.main() == 0
    assert save.call_args.args[-1] == ["🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f"]

with patch.object(kakera.sys, "argv", [
    "kakera.py", "--compose", "--tag", "topic/📚", "one", "two",
]), patch.object(kakera, "save_composed", return_value=(True, "saved")) as composed:
    assert kakera.main() == 0
    assert composed.call_args.args[-1] == ["topic/📚"]

with TemporaryDirectory() as directory:
    root = Path(directory)
    inbox = root / "queue.md"
    inbox.write_text("- [ ] #2024, #later. https://x.com/a/status/queue\n")
    with patch.object(kakera, "save", return_value=(True, "saved")) as save:
        assert kakera.process_inbox(inbox, None, root / "notes", root / "attachments") == 0
    assert save.call_args.args[-1] == ["later"]
    assert "[x]" in inbox.read_text()

for argv, function_name in (
    (["kakera.py", "--tag", "x", "--instagram-cookies", "personal"], "instagram_cookies"),
    (["kakera.py", "--tag", "x", "--twitter-cookies", "personal"], "twitter_cookies"),
    (["kakera.py", "--tag", "x", "--reddit-oauth", "user"], "reddit_oauth"),
):
    with patch.object(kakera.sys, "argv", argv), patch.object(kakera, function_name) as side_effect:
        try:
            kakera.main()
        except SystemExit as error:
            assert error.code == 2
        else:
            raise AssertionError("--tag was accepted by a non-capture command")
        assert not side_effect.called

with TemporaryDirectory() as directory:
    root = Path(directory)

    def fake_download(command, **_):
        target = Path(command[command.index("--directory") + 1]) / "remote-name.jpg"
        target.write_bytes(b"\xff\xd8\xff\xe0image")
        target.with_suffix(".jpg.json").write_text(
            json.dumps({"description": "Photo caption", "username": "artist"})
        )
        return CompletedProcess(command, 0, "", "")

    with (
        patch.object(kakera.subprocess, "run", side_effect=fake_download),
    ):
        url = "https://www.instagram.com/p/ABC_123/"
        assert kakera.save(url, None, root / "downloads", root / "attachments")[0]
        assert kakera.save(url, None, root / "downloads", root / "attachments")[0]
        assert [path.name for path in (root / "attachments" / "instagram").iterdir()] == [
            "instagram-ABC_123-01.jpg"
        ]
        assert "../attachments/instagram/instagram-ABC_123-01.jpg" in (
            root / "downloads" / "Photo caption - Instagram.md"
        ).read_text()
        assert kakera.save(
            "https://www.instagram.com/p/XYZ_789/", None, root / "downloads", root / "attachments"
        )[0]
        assert (root / "downloads" / "Photo caption - XYZ_789 - Instagram.md").exists()

    login_error = CompletedProcess(
        [], 1, "", "[instagram][error] HTTP redirect to login page (https://www.instagram.com/accounts/login/)"
    )
    with patch.object(kakera.subprocess, "run", return_value=login_error):
        assert kakera.save(url, None, root / "downloads", root / "attachments")[1].endswith(
            "retry with --browser safari"
        )

with TemporaryDirectory() as directory:
    root = Path(directory)

    def fake_download_without_title(command, **_):
        target = Path(command[command.index("--directory") + 1]) / "remote-name.jpg"
        target.write_bytes(b"\xff\xd8\xff\xe0image")
        target.with_suffix(".jpg.json").write_text(json.dumps({"username": "artist"}))
        return CompletedProcess(command, 0, "", "")

    with patch.object(kakera.subprocess, "run", side_effect=fake_download_without_title):
        assert kakera.save(
            "https://www.instagram.com/p/NO_TITLE_456/", None, root / "downloads", root / "attachments"
        )[0]

    note = root / "downloads" / "NO_TITLE_456 - Instagram.md"
    assert note.exists()
    assert 'title: "NO_TITLE_456 - Instagram"' in note.read_text()

with TemporaryDirectory() as directory:
    root = Path(directory)

    def fake_twitter_download(command, **_):
        target = Path(command[command.index("--directory") + 1]) / "remote-name.jpg"
        target.write_bytes(b"\xff\xd8\xff\xe0image")
        target.with_name("video.mp4").write_bytes(b"video")
        target.with_suffix(".jpg.json").write_text(json.dumps({
            "content": "\nA tweet caption\nsecond line",
            "author": {"nick": "A Display Name", "name": "artist"},
            "date": "2026-08-01T09:12:15+00:00",
        }))
        return CompletedProcess(command, 0, "", "")

    with patch.object(kakera.subprocess, "run", side_effect=fake_twitter_download) as run:
        url = "https://x.com/artist/status/123456789"
        assert kakera.save(url, "orion", root / "downloads", root / "attachments")[0]
        command = run.call_args.args[0]
        assert "--cookies-from-browser" in command
        assert "extractor.twitter.videos=false" in command
        assert "--filename" not in command
        assert command[command.index("--Print") + 1] == "after:{_path}"
        assert "extractor.twitter.replies=false" not in command
        assert kakera.save(url, "orion", root / "downloads", root / "attachments")[0]
        assert len(list((root / "attachments" / "twitter").glob("twitter-123456789-*"))) == 1
        assert kakera.save(
            "https://x.com/artist/status/987654321", "orion",
            root / "downloads", root / "attachments",
        )[0]

    note = root / "downloads" / "A tweet caption - Twitter.md"
    assert note.exists()
    text = note.read_text()
    assert 'title: "A tweet caption - Twitter"' in text
    assert 'twitter: "https://x.com/artist/status/123456789"' in text
    assert 'author: "A Display Name"' in text
    assert 'username: "artist"' in text
    assert 'author_url: "https://x.com/artist"' in text
    assert 'published: "2026-08-01T09:12:15+00:00"' in text
    assert 'tags:\n  - "twitter"' in text
    assert "A tweet caption\nsecond line" in text
    assert (root / "attachments" / "twitter" / "twitter-123456789-01.jpg").exists()

    assert (root / "downloads" / "A tweet caption - 987654321 - Twitter.md").exists()

with TemporaryDirectory() as directory:
    root = Path(directory)

    def fake_twitter_video_only(command, **_):
        target = Path(command[command.index("--directory") + 1]) / "video.mp4"
        target.write_bytes(b"not an image")
        return CompletedProcess(command, 0, "", "")

    with patch.object(kakera.subprocess, "run", side_effect=fake_twitter_video_only):
        success, message = kakera.save(
            "https://x.com/artist/status/123456789", None,
            root / "downloads", root / "attachments",
        )
    assert not success
    assert message == "no supported images found"

with TemporaryDirectory() as directory:
    root = Path(directory)

    def fake_twitter_alt_only(command, **_):
        target = Path(command[command.index("--directory") + 1]) / "remote-name.jpg"
        target.write_bytes(b"\xff\xd8\xff\xe0image")
        target.with_suffix(".jpg.json").write_text(json.dumps({"description": "Image alt text"}))
        return CompletedProcess(command, 0, "", "")

    with patch.object(kakera.subprocess, "run", side_effect=fake_twitter_alt_only):
        assert kakera.save(
            "https://x.com/artist/status/555555555", None,
            root / "downloads", root / "attachments",
        )[0]
    note = root / "downloads" / "555555555 - Twitter.md"
    assert note.exists()
    text = note.read_text()
    assert 'title: "555555555 - Twitter"' in text
    assert "Image alt text" not in text

with TemporaryDirectory() as directory:
    root = Path(directory)

    def fake_cookie_export(command, **_):
        exported = Path(command[command.index("--cookies-export") + 1])
        exported.write_text(
            "# Netscape HTTP Cookie File\n"
            ".instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tsecret\n"
            ".example.com\tTRUE\t/\tTRUE\t0\tsessionid\tdo-not-save\n"
        )
        return CompletedProcess(command, 0, "", "")

    with (
        patch.object(kakera, "COOKIE_DIR", root / ".cookies"),
        patch.object(kakera.subprocess, "run", side_effect=fake_cookie_export),
    ):
        assert instagram_cookies("personal") == 0
        cookie_file = root / ".cookies" / "instagram-personal.txt"
        assert "secret" in cookie_file.read_text()
        assert "do-not-save" not in cookie_file.read_text()
        assert cookie_file.stat().st_mode & 0o777 == 0o600

        with patch.object(kakera.subprocess, "run", side_effect=fake_download) as run:
            assert kakera.save(
                "https://www.instagram.com/p/ABC_123/",
                "orion",
                root / "downloads",
                root / "attachments",
                "personal",
            )[0]
        command = run.call_args.args[0]
        assert command[command.index("--cookies") + 1] == str(cookie_file)
        assert "--cookies-from-browser" not in command

with TemporaryDirectory() as directory:
    root = Path(directory)

    def fake_twitter_cookie_export(command, **_):
        exported = Path(command[command.index("--cookies-export") + 1])
        exported.write_text(
            "# Netscape HTTP Cookie File\n"
            ".x.com\tTRUE\t/\tTRUE\t0\tauth_token\tsecret-auth\n"
            ".twitter.com\tTRUE\t/\tTRUE\t0\tct0\tsecret-ct0\n"
            ".instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tdo-not-save\n"
            ".example.com\tTRUE\t/\tTRUE\t0\tct0\tdo-not-save\n"
        )
        return CompletedProcess(command, 0, "", "")

    with (
        patch.object(kakera, "COOKIE_DIR", root / ".cookies"),
        patch.object(kakera.subprocess, "run", side_effect=fake_twitter_cookie_export),
    ):
        assert twitter_cookies("imjma", "safari") == 0
        cookie_file = root / ".cookies" / "twitter-imjma.txt"
        text = cookie_file.read_text()
        assert "secret-auth" in text and "secret-ct0" in text
        assert "do-not-save" not in text
        assert (root / ".cookies").stat().st_mode & 0o777 == 0o700
        assert cookie_file.stat().st_mode & 0o777 == 0o600

        with patch.object(kakera.subprocess, "run", side_effect=fake_download) as run:
            assert kakera.save(
                "https://x.com/artist/status/123456789", None,
                root / "downloads", root / "attachments", None, "imjma",
            )[0]
        command = run.call_args.args[0]
        assert command[command.index("--cookies") + 1] == str(cookie_file)
        assert "--cookies-from-browser" not in command

        cookie_file.write_text(cookie_file.read_text())
        unavailable = CompletedProcess([], 1, "", "[twitter][error] 'Unavailable'")
        with patch.object(kakera.subprocess, "run", return_value=unavailable):
            success, message = kakera.save(
                "https://x.com/artist/status/987654321", None,
                root / "downloads", root / "attachments", None, "imjma",
            )
        assert not success
        assert message == "[twitter][error] 'Unavailable'"

    missing = root / ".cookies" / "twitter-missing.txt"
    def fake_missing_twitter_cookie_export(command, **_):
        Path(command[command.index("--cookies-export") + 1]).write_text(
            ".x.com\tTRUE\t/\tTRUE\t0\tauth_token\tsecret-auth\n"
        )
        return CompletedProcess(command, 0, "", "")

    with (
        patch.object(kakera, "COOKIE_DIR", root / ".cookies"),
        patch.object(kakera.subprocess, "run", side_effect=fake_missing_twitter_cookie_export),
    ):
        assert twitter_cookies("missing", "safari") == 1
        assert not missing.exists()

with TemporaryDirectory() as directory:
    config = Path(directory) / "kakera.json"
    config.write_text(json.dumps({"browser": "orion", "instagram": {"account": "ig"},
                                  "twitter": {"account": "saved"}}))
    with patch.object(kakera, "CONFIG", config):
        assert configured_twitter_account() == "saved"
        with patch.object(kakera, "save", return_value=(True, "saved")) as save:
            with patch.object(kakera.sys, "argv", ["kakera.py", "--browser", "safari", "https://x.com/u/status/123"]):
                assert main() == 0
        assert save.call_args.args[1:] == ("safari", Path(kakera.ROOT / "downloads"), Path(kakera.ROOT / "attachments"), None, None)

        with patch.object(kakera, "save", return_value=(True, "saved")) as save:
            with patch.object(kakera.sys, "argv", ["kakera.py", "https://x.com/u/status/123"]):
                assert main() == 0
        assert save.call_args.args[1] == "orion"
        assert save.call_args.args[5] == "saved"

        for option in ("--account", "--twitter-account"):
            with patch.object(kakera.sys, "argv", ["kakera.py", option, "../escape", "https://x.com/u/status/123"]):
                try:
                    main()
                except SystemExit as error:
                    assert error.code == 2
                else:
                    raise AssertionError(f"accepted invalid {option} alias")

with TemporaryDirectory() as directory:
    root = Path(directory)
    vault = root / "vault"
    vault.mkdir()
    config = root / "kakera.json"
    config.write_text(
        json.dumps(
            {
                "obsidian": {
                    "vault": str(vault),
                    "notes": "Clippings",
                    "attachments": "assets",
                    "inbox": "Kakera/inbox.md",
                },
                "browser": "orion",
                "instagram": {"account": "personal"},
                "reddit": {
                    "client_id": "client-id",
                    "user_agent": "Python:kakera:v0.1 (by /u/test)",
                },
            }
        )
    )
    with patch.object(kakera, "CONFIG", config):
        assert configured_browser() == "orion"
        assert configured_instagram_account() == "personal"
        assert configured_reddit() == ("client-id", "Python:kakera:v0.1 (by /u/test)")
        assert configured_folders() == (vault / "Clippings", vault / "assets")
        assert configured_inbox() == vault / "Kakera" / "inbox.md"
        with patch.object(kakera.subprocess, "run", side_effect=fake_download) as run:
            assert kakera.save(
                "https://redd.it/1abcxyz", None, vault / "Clippings", vault / "assets"
            )[0]
        command = run.call_args.args[0]
        assert "extractor.reddit.client-id=client-id" in command
        assert "extractor.reddit.user-agent=Python:kakera:v0.1 (by /u/test)" in command

    inbox = vault / "Kakera" / "inbox.md"
    inbox.parent.mkdir()
    inbox.write_text(
        "- [ ] https://www.instagram.com/p/ABC/\n"
        "- [ ] https://www.instagram.com/p/ABC/\n"
        "- [ ] http://xhslink.com/o/failed\n"
    )

    def save_from_inbox(url, *_arguments):
        if "instagram" in url:
            inbox.write_text(inbox.read_text() + "Obsidian edit during capture\n")
            return True, "saved"
        return False, "failed"

    with patch.object(kakera, "save", side_effect=save_from_inbox) as save:
        assert process_inbox(inbox, "orion", vault / "Clippings", vault / "assets") == 1
    assert inbox.read_text() == (
        "- [x] https://www.instagram.com/p/ABC/\n"
        "- [x] https://www.instagram.com/p/ABC/\n"
        "- [ ] http://xhslink.com/o/failed\n"
        "Obsidian edit during capture\n"
    )
    assert save.call_count == 2

    watched = vault / "Kakera" / "watched.md"
    watched.write_text("# Inbox\n")

    def change_then_stop(_interval):
        nonlocal_ticks[0] += 1
        if nonlocal_ticks[0] == 1:
            watched.write_text(watched.read_text() + "- [ ] https://redd.it/new\n")
        elif nonlocal_ticks[0] == 2:
            assert "- [x] https://redd.it/new" in watched.read_text()
            watched.unlink()
        else:
            assert not watched.exists()
            raise KeyboardInterrupt

    nonlocal_ticks = [0]
    with (
        patch.object(kakera, "save", return_value=(True, "saved")) as save,
        patch.object(kakera.time, "sleep", side_effect=change_then_stop),
    ):
        assert watch_inbox(watched, None, vault / "Clippings", vault / "assets", tags=["future"]) == 0
    assert save.call_count == 1
    assert save.call_args.args[-1] == ["future"]

    config.write_text(
        json.dumps(
            {
                "obsidian": {
                    "vault": str(vault),
                    "notes": "Clippings",
                    "attachments": "assets",
                    "inbox": "Kakera/inbox.md",
                },
                "todoist": {"project_id": "project-123"},
            }
        )
    )

    class TodoistResponse:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_arguments):
            return False

        def read(self):
            return self.body

    requests = []

    def fake_todoist_urlopen(request, **_arguments):
        requests.append((request.method, request.full_url))
        if request.method == "POST":
            return TodoistResponse(b"null")
        if "cursor=next" in request.full_url:
            return TodoistResponse(
                json.dumps(
                    {
                        "results": [
                            {"id": "task-2", "content": "No URL here", "description": "[capture](https://redd.it/BAD)"}
                        ],
                        "next_cursor": None,
                    }
                ).encode()
            )
        return TodoistResponse(
            json.dumps(
                {
                    "results": [
                        {"id": "task-1", "content": "[capture](https://www.instagram.com/p/GOOD/)"}
                    ],
                    "next_cursor": "next",
                }
            ).encode()
        )

    with (
        patch.object(kakera, "CONFIG", config),
        patch.object(kakera, "urlopen", side_effect=fake_todoist_urlopen),
        patch.dict(kakera.os.environ, {"TODOIST_API_TOKEN": "secret"}),
        patch.object(kakera, "save", side_effect=((True, "saved"), (False, "failed"))) as save,
    ):
        assert process_todoist("orion", vault / "Clippings", vault / "assets") == 1
    assert [call.args[0] for call in save.call_args_list] == [
        "https://www.instagram.com/p/GOOD/",
        "https://redd.it/BAD",
    ]
    assert [method for method, _url in requests] == ["GET", "POST", "GET"]
    assert "project_id=project-123" in requests[0][1]
    assert "cursor=next" in requests[2][1]

    config.write_text("[]")
    with patch.object(kakera, "CONFIG", config):
        try:
            configured_browser()
            raise AssertionError("non-object config was accepted")
        except ValueError:
            pass

    oauth_config = root / "oauth.json"
    oauth_config.write_text(json.dumps({"browser": "orion"}))
    with (
        patch.object(kakera, "CONFIG", oauth_config),
        patch.object(kakera.subprocess, "run", return_value=CompletedProcess([], 0)),
    ):
        assert reddit_oauth("u/test_user") == 0
    saved_config = json.loads(oauth_config.read_text())
    assert saved_config["browser"] == "orion"
    assert saved_config["reddit"] == {
        "client_id": kakera.REDDIT_CLIENT_ID,
        "user_agent": "Python:kakera:v0.1.0 (by /u/test_user)",
    }


with TemporaryDirectory() as directory:
    root = Path(directory)

    def fake_composition_download(command, **_arguments):
        url = command[-1]
        if "FAIL" in url:
            return CompletedProcess(command, 1, "", "[instagram][error] unavailable")
        target = Path(command[command.index("--directory") + 1]) / "remote.jpg"
        target.write_bytes(b"\xff\xd8\xff\xe0" + url.encode())
        target.with_suffix(".jpg.json").write_text(json.dumps({
            "description": f"caption for {url.rsplit('/', 2)[-2]}", "username": "artist"
        }))
        return CompletedProcess(command, 0, "", "")

    with patch.object(kakera.subprocess, "run", side_effect=fake_composition_download):
        assert kakera.save_composed(
            ["https://www.instagram.com/p/PRIMARY/", "https://www.instagram.com/p/SECOND/"],
            None, root / "notes", root / "attachments",
        )[0]
        note = next((root / "notes").glob("*.md"))
        text = note.read_text()
        assert "## Source 2 — Instagram" in text
        assert "caption for SECOND" in text
        assert "instagram-PRIMARY-01.jpg" in text and "instagram-SECOND-01.jpg" in text
        assert kakera.save_composed(
            ["https://www.instagram.com/p/PRIMARY/", "https://www.instagram.com/p/THIRD/"],
            None, root / "notes", root / "attachments",
        )[0]
        text = note.read_text()
        assert "Source 2 — Instagram" in text and "caption for THIRD" in text
        assert "caption for SECOND" not in text
        assert (root / "attachments" / "instagram" / "instagram-SECOND-01.jpg").exists()
        assert kakera.save_composed(
            ["https://www.instagram.com/p/PRIMARY/", "https://x.com/artist/status/999"],
            None, root / "notes", root / "attachments", tags=["Keep"],
        )[0]
        note.write_text(note.read_text().replace('  - "Keep"', '  - "Keep"\n  - "Manual"'))
        assert kakera.save_composed(
            ["https://www.instagram.com/p/PRIMARY/", "https://redd.it/100"],
            None, root / "notes", root / "attachments",
        )[0]
        recomposed_services = note.read_text().split("tags:", 1)[1]
        assert '  - "instagram"' in recomposed_services
        assert '  - "reddit"' in recomposed_services
        assert '  - "twitter"' not in recomposed_services
        assert '  - "Manual"' in recomposed_services
        assert kakera.save_composed(
            ["https://www.instagram.com/p/SECOND/", "https://www.instagram.com/p/THIRD/"],
            None, root / "notes", root / "attachments",
        )[0]
        b_note = next(path for path in (root / "notes").glob("*.md") if 'instagram: "https://www.instagram.com/p/SECOND/"' in path.read_text())
        assert b_note != note

    with patch.object(kakera.subprocess, "run", side_effect=fake_composition_download):
        success, message = kakera.save_composed(
            ["https://www.instagram.com/p/FAIL/", "https://www.instagram.com/p/SECOND/"],
            None, root / "notes", root / "attachments",
        )
    assert success and "1 source(s) failed" in message
    failed_note = next(path for path in (root / "notes").glob("*.md") if "FAIL" in path.read_text())
    assert "Failure: [instagram][error] unavailable" in failed_note.read_text()

    def reject_unsupported_download(command, **arguments):
        assert command[-1] != "https://example.com/profile"
        return fake_composition_download(command, **arguments)

    with patch.object(kakera.subprocess, "run", side_effect=reject_unsupported_download):
        success, message = kakera.save_composed(
            ["https://example.com/profile", "https://www.instagram.com/p/SECOND/"],
            None, root / "notes", root / "attachments",
        )
    assert success and "source(s) failed" in message
    unknown = next(path for path in (root / "notes").glob("*.md") if "source:" in path.read_text())
    assert "Unknown" in unknown.read_text()
    collision = root / "notes" / "collision.md"
    collision.write_text(
        "---\n"
        "title: \"https://www.instagram.com/p/COLLIDE/\"\n"
        "author_url: \"https://www.instagram.com/p/COLLIDE/\"\n"
        "instagram: \"https://www.instagram.com/p/OTHER/\"\n"
        "---\n"
    )
    collision_candidate = kakera.composed_note_path(
        root / "notes",
        {"name": "instagram-COLLIDE", "service": "instagram",
         "url": "https://www.instagram.com/p/COLLIDE/", "metadata": {"description": "new"}},
    )
    assert collision_candidate != collision

    unsupported_url = "https://example.com/unsupported-stable"
    with patch.object(kakera.subprocess, "run", side_effect=reject_unsupported_download):
        assert kakera.save_composed(
            [unsupported_url, "https://www.instagram.com/p/SECOND/"],
            None, root / "notes", root / "attachments",
        )[0]
    unknown_notes = [path for path in (root / "notes").glob("*.md") if f'source: "{unsupported_url}"' in path.read_text()]
    assert len(unknown_notes) == 1 and "Unknown" in unknown_notes[0].read_text()
    unsupported_note = unknown_notes[0]
    unsupported_hash = hashlib.sha256(kakera.canonical_url(unsupported_url).encode()).hexdigest()[:12]
    with patch.object(kakera.subprocess, "run", side_effect=reject_unsupported_download):
        assert kakera.save_composed(
            [unsupported_url, "https://www.instagram.com/p/THIRD/"],
            None, root / "notes", root / "attachments",
        )[0]
    recomposed_unknown = [path for path in (root / "notes").glob("*.md") if f'source: "{unsupported_url}"' in path.read_text()]
    assert recomposed_unknown == [unsupported_note]
    recomposed_text = unsupported_note.read_text()
    assert unsupported_hash in recomposed_text and "THIRD" in recomposed_text
    assert "SECOND" not in recomposed_text and "author:" not in recomposed_text and "author_url:" not in recomposed_text

    metadata_note = root / "notes" / "metadata-composed.md"
    primary_image = root / "attachments" / "instagram" / "metadata-primary.jpg"
    additional_image = root / "attachments" / "twitter" / "metadata-additional.jpg"
    primary_image.parent.mkdir(parents=True, exist_ok=True)
    additional_image.parent.mkdir(parents=True, exist_ok=True)
    primary_image.write_bytes(b"\xff\xd8\xff\xe0primary")
    additional_image.write_bytes(b"\xff\xd8\xff\xe0additional")
    kakera.write_composed_note(
        metadata_note,
        [
            {"name": "instagram-META", "service": "instagram",
             "url": "https://www.instagram.com/p/META/",
             "metadata": {"description": "primary text", "fullname": "Primary Artist",
                          "username": "primary", "author_url": "https://instagram.test/primary",
                          "published": "2026-01-01"}, "images": [primary_image]},
            {"name": "twitter-META2", "service": "twitter",
             "url": "https://x.com/artist/status/990",
             "metadata": {"content": "additional text", "author": "Additional Artist",
                          "username": "additional", "author_url": "https://x.com/additional",
                          "published": "2026-01-02"}, "images": [additional_image]},
            {"name": "reddit-FAILED", "service": "reddit",
             "url": "https://redd.it/failed", "metadata": {}, "images": [],
             "error": "unavailable"},
        ],
        ["instagram", "twitter"],
    )
    metadata_text = metadata_note.read_text()
    assert metadata_text.count('instagram: "https://www.instagram.com/p/META/"') == 1
    assert "## Source 1" not in metadata_text and "Primary Artist" in metadata_text
    for field in ("URL", "Title", "Author", "Username", "Author URL", "Published"):
        assert f"- {field}:" in metadata_text
    assert "additional text" in metadata_text
    assert "  - reddit" not in metadata_text.split("tags:", 1)[1]

    for profile in ("https://www.instagram.com/artist/", "https://www.reddit.com/user/artist/"):
        try:
            kakera.capture_id(profile)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted profile URL: {profile}")

    inbox = root / "inbox.md"
    inbox.write_text(
        "- [ ] Compose\n"
        "  - [ ] https://www.instagram.com/p/ONE/\n"
        "    - [ ] https://www.instagram.com/p/TWO/\n"
    )
    with patch.object(kakera, "save_composed", return_value=(True, "saved 2 image(s)")) as composed:
        assert process_inbox(inbox, None, root / "notes2", root / "attachments2") == 0
    assert composed.call_args.args[0] == [
        "https://www.instagram.com/p/ONE/", "https://www.instagram.com/p/TWO/"
    ]
    assert all(line.startswith("  ") or "[x]" in line for line in inbox.read_text().splitlines())

    single_line = root / "single-line-inbox.md"
    single_line.write_text("- [ ] https://x.com/a/status/10 https://x.com/a/status/11\n")
    with patch.object(kakera, "save_composed", return_value=(True, "saved")) as composed:
        assert process_inbox(single_line, None, root / "notes2", root / "attachments2") == 0
    assert composed.call_args.args[0] == ["https://x.com/a/status/10", "https://x.com/a/status/11"]
    assert "[x]" in single_line.read_text()

    url_parent = root / "url-parent-inbox.md"
    url_parent.write_text(
        "- [ ] https://x.com/a/status/12\n"
        "  - [ ] https://x.com/a/status/13\n"
    )
    with patch.object(kakera, "save_composed", return_value=(True, "saved")) as composed:
        assert process_inbox(url_parent, None, root / "notes2", root / "attachments2") == 0
    assert composed.call_args.args[0] == ["https://x.com/a/status/12", "https://x.com/a/status/13"]
    assert url_parent.read_text().count("[x]") == 2

    different_order = root / "different-order-inbox.md"
    different_order.write_text(
        "- [ ] https://x.com/a/status/14 https://x.com/a/status/15\n"
        "- [ ] https://x.com/a/status/15 https://x.com/a/status/14\n"
    )
    with patch.object(kakera, "save_composed", return_value=(True, "saved")) as composed:
        assert process_inbox(different_order, None, root / "notes2", root / "attachments2") == 0
    assert composed.call_count == 2 and different_order.read_text().count("[x]") == 2

    partial_inbox = root / "partial-inbox.md"
    partial_inbox.write_text("- [ ] https://x.com/a/status/16 https://x.com/a/status/17\n")
    with patch.object(kakera, "save_composed", return_value=(True, "one source saved")):
        assert process_inbox(partial_inbox, None, root / "notes2", root / "attachments2") == 0
    assert "[x]" in partial_inbox.read_text()

    zero_inbox = root / "zero-inbox.md"
    zero_inbox.write_text("- [ ] https://x.com/a/status/18 https://x.com/a/status/19\n")
    with patch.object(kakera, "save_composed", return_value=(False, "no images")):
        assert process_inbox(zero_inbox, None, root / "notes2", root / "attachments2") == 1
    assert "[ ]" in zero_inbox.read_text() and "[x]" not in zero_inbox.read_text()

    config = root / "todoist.json"
    config.write_text(json.dumps({"todoist": {"project_id": "p"}}))
    todoist_requests = []

    def fake_hierarchical_todoist(request, **_arguments):
        todoist_requests.append((request.method, request.full_url))
        if request.method == "POST":
            return TodoistResponse(b"null")
        if "cursor=child" in request.full_url:
            return TodoistResponse(json.dumps({"results": [{
                "id": "child", "parent_id": "root", "child_order": 1,
                "content": "https://www.instagram.com/p/CHILD/"
            }], "next_cursor": None}).encode())
        return TodoistResponse(json.dumps({"results": [{
            "id": "root", "parent_id": None, "child_order": 0,
            "content": "https://www.instagram.com/p/ROOT/"
        }], "next_cursor": "child"}).encode())

    with (
        patch.object(kakera, "CONFIG", config),
        patch.object(kakera, "urlopen", side_effect=fake_hierarchical_todoist),
        patch.dict(kakera.os.environ, {"TODOIST_API_TOKEN": "secret"}),
        patch.object(kakera, "save_composed", return_value=(True, "saved")) as composed,
    ):
        assert process_todoist(None, root / "notes3", root / "attachments3") == 0
    assert composed.call_args.args[0] == [
        "https://www.instagram.com/p/ROOT/", "https://www.instagram.com/p/CHILD/"
    ]
    assert [method for method, _url in todoist_requests] == ["GET", "GET", "POST"]


with TemporaryDirectory() as directory:
    root = Path(directory)
    with (
        patch.object(kakera, "save", return_value=(True, "saved")) as save,
        patch.object(kakera, "save_composed", return_value=(True, "composed")) as composed,
        patch.object(kakera.sys, "argv", ["kakera.py", "one", "two"]),
    ):
        assert main() == 0
        assert save.call_count == 2 and not composed.called
    with (
        patch.object(kakera, "save", return_value=(True, "saved")) as save,
        patch.object(kakera, "save_composed", return_value=(True, "composed")) as composed,
        patch.object(kakera.sys, "argv", ["kakera.py", "--compose", "one", "two"]),
    ):
        assert main() == 0
        assert not save.called and composed.call_args.args[0] == ["one", "two"]

    duplicate = ["https://x.com/a/status/1?utm_source=x", "https://x.com/a/status/1/"]
    with patch.object(kakera, "save", return_value=(True, "saved")) as save:
        assert kakera.save_composed(duplicate, None, root / "notes", root / "attachments")[0]
        assert save.call_args.args[0] == duplicate[0]
    instagram_duplicate = [
        "https://www.instagram.com/p/DbqJEsLna8A/?foo=bar",
        "https://www.instagram.com/p/DbqJEsLna8A/",
    ]
    with patch.object(kakera, "save", return_value=(True, "saved")) as save:
        assert kakera.save_composed(
            instagram_duplicate, None, root / "notes", root / "attachments"
        )[0]
        assert save.call_args.args[0] == instagram_duplicate[0]

    existing = root / "notes" / "existing.md"
    existing.parent.mkdir()
    existing.write_text("old note")
    with patch.object(kakera.subprocess, "run", return_value=CompletedProcess([], 0, "", "")):
        success, _ = kakera.save_composed(
            ["https://www.instagram.com/p/EMPTY/", "https://x.com/a/status/2"],
            None, root / "notes", root / "attachments",
        )
    assert not success and existing.read_text() == "old note"

    def mixed_download(command, **_arguments):
        if "PRIMARYFAIL" in command[-1]:
            return CompletedProcess(command, 1, "", "[instagram][error] unavailable")
        directory = Path(command[command.index("--directory") + 1])
        image = directory / "image.jpg"
        image.write_bytes(b"\xff\xd8\xff\xe0x")
        image.with_suffix(".jpg.json").write_text(json.dumps({"content": "tweet", "author": {"name": "x", "nick": "X"}}))
        return CompletedProcess(command, 0, "", "")

    with patch.object(kakera.subprocess, "run", side_effect=mixed_download):
        assert kakera.save_composed(
            ["https://www.instagram.com/p/PRIMARYFAIL/", "https://x.com/x/status/22"],
            None, root / "notes", root / "attachments",
        )[0]
    mixed_note = next(path for path in (root / "notes").glob("*.md") if "PRIMARYFAIL" in path.read_text())
    mixed_text = mixed_note.read_text()
    assert '  - "instagram"' in mixed_text and '  - "twitter"' in mixed_text

    def ordered_download(command, **_arguments):
        directory = Path(command[command.index("--directory") + 1])
        paths = []
        for number in (10, 2, 11, 1):
            image = directory / f"{number:06}.jpg"
            image.write_bytes(b"\xff\xd8\xff\xe0" + bytes([number]))
            image.with_suffix(".jpg.json").write_text(json.dumps({"description": f"caption {number}"}))
            paths.append(image)
        return CompletedProcess(command, 0, "\n".join(str(path) for path in (paths[3], paths[1], paths[0], paths[2])), "")

    with patch.object(kakera.subprocess, "run", side_effect=ordered_download):
        assert kakera.save(
            "https://www.instagram.com/p/ORDERED/", None,
            root / "notes", root / "attachments",
        )[0]
    attachment_dir = root / "attachments" / "instagram"
    ordered = [path.read_bytes()[-1] for path in sorted(attachment_dir.glob("instagram-ORDERED-*"),
                                                        key=lambda path: int(path.stem.rsplit("-", 1)[1]))]
    assert ordered == [1, 2, 10, 11]
    assert 'title: "caption 1 - Instagram"' in (root / "notes" / "caption 1 - Instagram.md").read_text()

    normalized_root = root / "normalized-fetch"
    normalized_url = "https://www.instagram.com/p/DbqJEsLna8A/"
    indexed_url = normalized_url + "?foo=bar&igsh=tracking#comments"
    def normalized_download(command, **_arguments):
        assert command[-1] == normalized_url
        assert not any("range" in str(item).casefold() for item in command)
        directory = Path(command[command.index("--directory") + 1])
        paths = []
        for index in range(3):
            image = directory / f"carousel-{index}.jpg"
            image.write_bytes(b"\xff\xd8\xff\xe0" + bytes([index]))
            paths.append(image)
        paths[0].with_suffix(".jpg.json").write_text(json.dumps({"title": "Carousel"}))
        return CompletedProcess(command, 0, "\n".join(str(path) for path in paths), "")
    with patch.object(kakera.subprocess, "run", side_effect=normalized_download):
        normalized_source, normalized_error = kakera.fetch_source(
            indexed_url, None, None, None, normalized_root, "instagram-DbqJEsLna8A"
        )
    assert normalized_error is None
    assert normalized_source["url"] == normalized_url
    assert normalized_source["metadata"]["post_url"] == normalized_url
    assert len(normalized_source["valid"]) == 3

    number_root = root / "number-boundary"
    number_dir = number_root / "attachments" / "instagram"
    number_dir.mkdir(parents=True)
    for number in (99, 100, 101):
        existing = number_dir / f"instagram-NUMBERS-{number:02}.jpg"
        existing.write_bytes(b"\xff\xd8\xff\xe0existing" + str(number).encode())
    number_102 = root / "photo-2.jpg"
    number_103 = root / "photo-10.jpg"
    number_102.write_bytes(b"\xff\xd8\xff\xe0new-102")
    number_103.write_bytes(b"\xff\xd8\xff\xe0new-103")
    number_source = {"name": "instagram-NUMBERS", "service": "instagram",
                     "valid": [(number_102, ".jpg"), (number_103, ".jpg")]}
    number_images = kakera.store_source_images(number_source, number_root / "notes", number_root / "attachments")
    assert [path.name for path in number_images[-2:]] == [
        "instagram-NUMBERS-102.jpg", "instagram-NUMBERS-103.jpg"
    ]
    repeat_source = {"name": "instagram-NUMBERS", "service": "instagram",
                     "valid": [(number_102, ".jpg"), (number_103, ".jpg")]}
    assert len(kakera.store_source_images(repeat_source, number_root / "notes", number_root / "attachments")) == 5

    delegated_root = root / "delegated-extract"
    (delegated_root / "child-one").mkdir(parents=True)
    (delegated_root / "child-two").mkdir()
    def delegated_download(command, **_arguments):
        first = delegated_root / "child-one" / "photo.jpg"
        second = delegated_root / "child-two" / "photo.jpg"
        first.write_bytes(b"\xff\xd8\xff\xe0first")
        second.write_bytes(b"\xff\xd8\xff\xe0second")
        first.with_suffix(".jpg.json").write_text(json.dumps({"description": "first delegated"}))
        second.with_suffix(".jpg.json").write_text(json.dumps({"description": "second delegated"}))
        return CompletedProcess(command, 0, f"{first}\n{second}\n", "")
    with patch.object(kakera.subprocess, "run", side_effect=delegated_download):
        delegated_source, delegated_error = kakera.fetch_source(
            "https://www.instagram.com/p/DELEGATED/", None, None, None,
            delegated_root, "instagram-DELEGATED",
        )
    assert delegated_error is None and [path.name for path, _ in delegated_source["valid"]] == ["photo.jpg", "photo.jpg"]
    delegated_images = kakera.store_source_images(
        delegated_source, root / "notes", root / "attachments"
    )
    assert len(delegated_images) == 2 and {path.read_bytes()[-1] for path in delegated_images} == {ord("t"), ord("d")}

    persistence_root = root / "persistence"
    persistence_root.mkdir()
    persisted_image = persistence_root / "instagram-OK-01.jpg"
    persisted_image.write_bytes(b"\xff\xd8\xff\xe0ok")
    def fail_first_store(source, notes_path, *_arguments):
        notes_path.mkdir(parents=True, exist_ok=True)
        if source["name"].endswith("BADSTORE"):
            raise OSError("simulated disk error")
        source["images"] = [persisted_image]
        return source["images"]
    with patch.object(kakera.subprocess, "run", side_effect=ordered_download), \
         patch.object(kakera, "store_source_images", side_effect=fail_first_store):
        success, message = kakera.save_composed(
            ["https://www.instagram.com/p/BADSTORE/", "https://www.instagram.com/p/OKSTORE/"],
            None, persistence_root / "notes", persistence_root / "attachments",
        )
    assert success and "1 source(s) failed" in message
    persisted_note = next((persistence_root / "notes").glob("*.md"))
    assert "simulated disk error" in persisted_note.read_text()

    def two_image_download(command, **_arguments):
        directory = Path(command[command.index("--directory") + 1])
        paths = []
        for label in ("first", "second"):
            image = directory / f"{label}.jpg"
            image.write_bytes(b"\xff\xd8\xff\xe0" + label.encode())
            paths.append(image)
        return CompletedProcess(command, 0, "\n".join(str(path) for path in paths), "")

    real_copyfileobj = kakera.shutil.copyfileobj
    copy_calls = [0]
    def fail_second_copy(source_file, target_file, *args):
        copy_calls[0] += 1
        if copy_calls[0] == 2:
            raise OSError("second file failed")
        return real_copyfileobj(source_file, target_file, *args)
    atomic_root = root / "atomic"
    with (
        patch.object(kakera.subprocess, "run", side_effect=two_image_download),
        patch.object(kakera.shutil, "copyfileobj", side_effect=fail_second_copy),
    ):
        success, message = kakera.save_composed(
            ["https://www.instagram.com/p/ATOMIC/", "https://x.com/a/status/123456"],
            None, atomic_root / "notes", atomic_root / "attachments",
        )
    assert success and "1 source(s) failed" in message
    atomic_note = next((atomic_root / "notes").glob("*.md"))
    atomic_text = atomic_note.read_text()
    assert "Warning: cannot save attachments: second file failed" in atomic_text
    assert "instagram-ATOMIC-01.jpg" in atomic_text
    assert not list((atomic_root / "attachments" / "instagram").glob(".*"))

    all_fail_root = root / "all-fail"
    with (
        patch.object(kakera.subprocess, "run", side_effect=two_image_download),
        patch.object(kakera.shutil, "copyfileobj", side_effect=OSError("all files failed")),
    ):
        success, _ = kakera.save_composed(
            ["https://www.instagram.com/p/ALLFAIL/", "https://x.com/a/status/123457"],
            None, all_fail_root / "notes", all_fail_root / "attachments",
        )
    assert not success
    assert not list((all_fail_root / "notes").glob("*.md"))
    assert not list(all_fail_root.rglob(".*-*.jpg"))
    assert not (all_fail_root / "attachments").exists()
    assert not (all_fail_root / "attachments" / "instagram").exists()
    assert not (all_fail_root / "attachments" / "twitter").exists()

    reusable_root = root / "reusable"
    reusable_dir = reusable_root / "attachments" / "instagram"
    reusable_dir.mkdir(parents=True)
    reusable_image = reusable_dir / "instagram-REUSE-01.jpg"
    reusable_image.write_bytes(b"\xff\xd8\xff\xe0existing")
    reusable_source = {"name": "instagram-REUSE", "service": "instagram",
                       "valid": [(reusable_root / "missing.jpg", ".jpg")]}
    reused = kakera.store_source_images(reusable_source, reusable_root / "notes", reusable_root / "attachments")
    assert reused == [reusable_image] and "cannot read downloaded image" in reusable_source["error"]

    ordinary_root = root / "ordinary-persistence"
    ordinary_image = ordinary_root / "persisted.jpg"
    ordinary_image.parent.mkdir(parents=True)
    ordinary_image.write_bytes(b"\xff\xd8\xff\xe0persisted")
    def partial_store(source, *_arguments):
        source["images"] = [ordinary_image]
        source["error"] = "cannot save attachments: ordinary failure"
        return source["images"]
    with patch.object(kakera.subprocess, "run", side_effect=fake_download), \
         patch.object(kakera, "store_source_images", side_effect=partial_store):
        success, message = kakera.save(
            "https://www.instagram.com/p/ORDINARYFAIL/", None,
            ordinary_root / "notes", ordinary_root / "attachments",
        )
    assert not success and "ordinary failure" in message
    assert not (ordinary_root / "notes").exists()

    ordinary_empty = root / "ordinary-empty"
    def empty_store(source, _notes, attachment_root):
        service_dir = attachment_root / source["service"]
        service_dir.mkdir(parents=True, exist_ok=True)
        source["_created_attachment_dir"] = True
        source["error"] = "cannot save attachments: empty"
        source["images"] = []
        return []
    with patch.object(kakera.subprocess, "run", side_effect=fake_download), \
         patch.object(kakera, "store_source_images", side_effect=empty_store):
        success, _ = kakera.save(
            "https://www.instagram.com/p/ORDINARYEMPTY/", None,
            ordinary_empty / "notes", ordinary_empty / "attachments",
        )
    assert not success and not (ordinary_empty / "attachments" / "instagram").exists()

    boundary = root / "boundary"
    extraction = boundary / "extract"
    outside = boundary / "outside.jpg"
    extraction.mkdir(parents=True)
    outside.write_bytes(b"\xff\xd8\xff\xe0outside")
    safe = extraction / "safe.jpg"
    safe.write_bytes(b"\xff\xd8\xff\xe0safe")
    escape = extraction / "escape.jpg"
    escape.symlink_to(outside)
    def path_probe(command, **_arguments):
        return CompletedProcess(command, 0, "\n".join((
            str(outside), "../outside.jpg", str(escape), "safe.jpg"
        )), "")
    with patch.object(kakera.subprocess, "run", side_effect=path_probe):
        source, error = kakera.fetch_source(
            "https://www.instagram.com/p/BOUNDARY/", None, None, None, extraction,
            "instagram-BOUNDARY",
        )
    assert error is None and [path.name for path, _ in source["valid"]] == ["safe.jpg"]

    concurrent_root = root / "concurrent-attachments"
    incoming_a = root / "incoming-a.jpg"
    incoming_b = root / "incoming-b.jpg"
    incoming_a.write_bytes(b"\xff\xd8\xff\xe0A")
    incoming_b.write_bytes(b"\xff\xd8\xff\xe0B")
    source_a = {"name": "instagram-CONCURRENT", "service": "instagram",
                "valid": [(incoming_a, ".jpg")]}
    source_b = {"name": "instagram-CONCURRENT", "service": "instagram",
                "valid": [(incoming_b, ".jpg")]}
    errors = []
    def persist(source):
        try:
            kakera.store_source_images(source, root / "notes", concurrent_root)
        except Exception as error:
            errors.append(error)
    threads = [Thread(target=persist, args=(source,)) for source in (source_a, source_b)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    persisted = list((concurrent_root / "instagram").glob("instagram-CONCURRENT-*.jpg"))
    assert not errors and len(persisted) == 2
    assert {path.read_bytes()[-1] for path in persisted} == {ord("A"), ord("B")}

    cross_root = root / "cross-extension"
    jpg = root / "cross.jpg"
    png = root / "cross.png"
    jpg.write_bytes(b"\xff\xd8\xff\xe0same")
    png.write_bytes(b"\xff\xd8\xff\xe0same")
    first = {"name": "instagram-CROSS", "service": "instagram", "valid": [(jpg, ".jpg")]}
    second = {"name": "instagram-CROSS", "service": "instagram", "valid": [(png, ".png")]}
    kakera.store_source_images(first, root / "notes", cross_root)
    kakera.store_source_images(second, root / "notes", cross_root)
    assert len(list((cross_root / "instagram").glob("instagram-CROSS-*"))) == 1
    different = root / "cross-different.png"
    different.write_bytes(b"\x89PNG\r\n\x1a\ndifferent")
    third = {"name": "instagram-CROSS", "service": "instagram", "valid": [(different, ".png")]}
    kakera.store_source_images(third, root / "notes", cross_root)
    assert len(list((cross_root / "instagram").glob("instagram-CROSS-*"))) == 2

    process_root = root / "process-attachments"
    process_a = root / "process-a.jpg"
    process_b = root / "process-b.jpg"
    process_a.write_bytes(b"\xff\xd8\xff\xe0process-a")
    process_b.write_bytes(b"\xff\xd8\xff\xe0process-b")
    context = mp.get_context("fork")
    queue = context.Queue()
    workers = [
        context.Process(target=_process_store_worker,
                        args=(path, process_root, extension, queue))
        for path, extension in ((process_a, ".jpg"), (process_b, ".png"))
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert not worker.is_alive()
    process_results = [queue.get(timeout=2) for _ in workers]
    assert all(result[0] == "ok" for result in process_results)
    process_files = list((process_root / "instagram").glob("instagram-MP-*"))
    assert len(process_files) == 2 and {path.read_bytes()[-1] for path in process_files} == {ord("a"), ord("b")}

    process_same_root = root / "process-same-attachments"
    process_same_jpg = root / "process-same.jpg"
    process_same_png = root / "process-same.png"
    process_same_jpg.write_bytes(b"\xff\xd8\xff\xe0same-process")
    process_same_png.write_bytes(b"\xff\xd8\xff\xe0same-process")
    queue = context.Queue()
    workers = [
        context.Process(target=_process_store_worker,
                        args=(path, process_same_root, extension, queue, "instagram-MPSAME"))
        for path, extension in ((process_same_jpg, ".jpg"), (process_same_png, ".png"))
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert not worker.is_alive()
    process_results = [queue.get(timeout=2) for _ in workers]
    assert all(result[0] == "ok" for result in process_results)
    assert len(list((process_same_root / "instagram").glob("instagram-MPSAME-*"))) == 1

    lock_parent = root / "lock-runtime"
    lock_parent.mkdir()
    lock_name = f"kakera-locks-{os.getuid()}"
    with patch.object(kakera.tempfile, "gettempdir", return_value=str(lock_parent)):
        (lock_parent / lock_name).symlink_to(root / "outside-lock-target", target_is_directory=True)
        try:
            with kakera.capture_lock(root / "lock-output", "instagram", "instagram-LOCK"):
                raise AssertionError("accepted symlink lock directory")
        except OSError:
            pass
    (lock_parent / lock_name).unlink()
    (lock_parent / lock_name).mkdir()
    os.chmod(lock_parent / lock_name, 0o755)
    with patch.object(kakera.tempfile, "gettempdir", return_value=str(lock_parent)):
        try:
            with kakera.capture_lock(root / "lock-output", "instagram", "instagram-LOCK"):
                raise AssertionError("accepted writable lock directory")
        except OSError:
            pass
    (lock_parent / lock_name).rmdir()
    (lock_parent / lock_name).mkdir(mode=0o700)
    lock_filename = hashlib.sha256(kakera.canonical_lock_path(root / "lock-output").encode()).hexdigest() + ".lock"
    os.mkfifo(lock_parent / lock_name / lock_filename)
    with patch.object(kakera.tempfile, "gettempdir", return_value=str(lock_parent)):
        try:
            with kakera.capture_lock(root / "lock-output", "instagram", "instagram-FIFO"):
                raise AssertionError("accepted FIFO lock file")
        except OSError:
            pass
    (lock_parent / lock_name / lock_filename).unlink()
    symlink_filename = hashlib.sha256(kakera.canonical_lock_path(root / "lock-output").encode()).hexdigest() + ".lock"
    (root / "outside-lock-file").write_text("outside")
    (lock_parent / lock_name / symlink_filename).symlink_to(root / "outside-lock-file")
    with patch.object(kakera.tempfile, "gettempdir", return_value=str(lock_parent)):
        try:
            with kakera.capture_lock(root / "lock-output", "instagram", "instagram-SYMLINK"):
                raise AssertionError("accepted symlink lock file")
        except OSError:
            pass
    assert not (root / "lock-output").exists()

    assert kakera.canonical_lock_path(root / "ATTACHMENTS") == kakera.canonical_lock_path(root / "attachments")
    composed_unicode = root / "Cafe\u0301"
    composed_unicode_alias = root / "Caf\u00e9"
    assert kakera.canonical_lock_path(composed_unicode) == kakera.canonical_lock_path(composed_unicode_alias)

    hardlink_path = lock_parent / lock_name / symlink_filename
    hardlink_path.unlink()
    os.chmod(root / "outside-lock-file", 0o600)
    os.link(root / "outside-lock-file", hardlink_path)
    with patch.object(kakera.tempfile, "gettempdir", return_value=str(lock_parent)):
        try:
            with kakera.capture_lock(root / "lock-output", "instagram", "instagram-SYMLINK"):
                raise AssertionError("accepted hardlinked lock file")
        except OSError:
            pass
    hardlink_path.unlink()
    hardlink_path.write_text("wrong mode")
    os.chmod(hardlink_path, 0o644)
    with patch.object(kakera.tempfile, "gettempdir", return_value=str(lock_parent)):
        try:
            with kakera.capture_lock(root / "lock-output", "instagram", "instagram-SYMLINK"):
                raise AssertionError("accepted writable lock file")
        except OSError:
            pass
    real_uid = os.getuid()
    with (
        patch.object(kakera.tempfile, "gettempdir", return_value=str(lock_parent)),
        patch.object(kakera.os, "getuid", side_effect=(real_uid, real_uid + 1)),
    ):
        try:
            with kakera.capture_lock(root / "lock-output", "instagram", "instagram-OWNER"):
                raise AssertionError("accepted lock directory with another owner")
        except OSError:
            pass
    fstat_calls = [0]
    original_fstat = kakera.os.fstat
    def wrong_lock_owner(file_descriptor):
        result = original_fstat(file_descriptor)
        fstat_calls[0] += 1
        if fstat_calls[0] == 2:
            return SimpleNamespace(st_mode=result.st_mode, st_uid=real_uid + 1, st_nlink=result.st_nlink)
        return result
    with (
        patch.object(kakera.tempfile, "gettempdir", return_value=str(lock_parent)),
        patch.object(kakera.os, "fstat", side_effect=wrong_lock_owner),
    ):
        try:
            with kakera.capture_lock(root / "lock-output", "instagram", "instagram-LOCK-OWNER"):
                raise AssertionError("accepted lock file with another owner")
        except OSError:
            pass
    assert fstat_calls[0] >= 2
    hardlink_path.unlink()
    with (
        patch.object(kakera.tempfile, "gettempdir", return_value=str(lock_parent)),
        patch.object(kakera.fcntl, "flock", side_effect=BlockingIOError),
        patch.object(kakera.time, "monotonic", side_effect=(0, 31)),
    ):
        try:
            with kakera.capture_lock(root / "lock-output", "instagram", "instagram-TIMEOUT"):
                raise AssertionError("lock timeout did not fail")
        except OSError as error:
            assert "timed out" in str(error)

    cleanup_root = root / "cleanup-race"
    cleanup_input = root / "cleanup-race.jpg"
    cleanup_input.write_bytes(b"\xff\xd8\xff\xe0cleanup")
    cleanup_source = {"name": "instagram-CLEANUP", "service": "instagram",
                      "valid": [(cleanup_input, ".jpg")], "_created_attachment_dir": True}
    cleanup_threads = [
        Thread(target=kakera.cleanup_empty_attachment_dirs,
               args=([cleanup_source], cleanup_root)),
        Thread(target=kakera.store_source_images,
               args=({"name": "instagram-CLEANUP", "service": "instagram",
                      "valid": [(cleanup_input, ".jpg")]}, root / "notes", cleanup_root)),
    ]
    for thread in cleanup_threads:
        thread.start()
    for thread in cleanup_threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert list((cleanup_root / "instagram").glob("instagram-CLEANUP-*.jpg"))

    dangerous_root = root / "deterministic-cleanup-race"
    dangerous_input = root / "deterministic-cleanup.jpg"
    dangerous_input.write_bytes(b"\xff\xd8\xff\xe0danger")
    writer_entered = Event()
    cleanup_entered = Event()
    release_writer = Event()
    writer_errors, cleanup_errors = [], []
    original_mkdir = Path.mkdir
    original_capture_lock = kakera.capture_lock
    def blocked_service_mkdir(path, *args, **kwargs):
        result = original_mkdir(path, *args, **kwargs)
        if path == dangerous_root / "instagram" and not writer_entered.is_set():
            writer_entered.set()
            assert release_writer.wait(timeout=10)
        return result
    @contextmanager
    def observed_cleanup_lock(path, service, name):
        if service == "cleanup":
            cleanup_entered.set()
        with original_capture_lock(path, service, name):
            yield
    dangerous_source = {"name": "instagram-DANGEROUS", "service": "instagram",
                        "valid": [(dangerous_input, ".jpg")]}
    def dangerous_writer():
        try:
            kakera.store_source_images(dangerous_source, root / "notes", dangerous_root)
        except Exception as error:
            writer_errors.append(error)
    def dangerous_cleanup():
        try:
            kakera.cleanup_empty_attachment_dirs(
                [{"service": "instagram", "_created_attachment_dir": True}], dangerous_root
            )
        except Exception as error:
            cleanup_errors.append(error)
    with (
        patch.object(Path, "mkdir", autospec=True, side_effect=blocked_service_mkdir),
        patch.object(kakera, "capture_lock", side_effect=observed_cleanup_lock),
    ):
        writer_thread = Thread(target=dangerous_writer)
        cleanup_thread = Thread(target=dangerous_cleanup)
        writer_thread.start()
        assert writer_entered.wait(timeout=10)
        cleanup_thread.start()
        assert cleanup_entered.wait(timeout=10)
        release_writer.set()
        writer_thread.join(timeout=10)
        cleanup_thread.join(timeout=10)
        assert not writer_thread.is_alive() and not cleanup_thread.is_alive()
    assert not writer_errors and not cleanup_errors
    assert list((dangerous_root / "instagram").glob("instagram-DANGEROUS-*.jpg"))

    ordinary_race = root / "ordinary-note-race"
    ordinary_race_notes = ordinary_race / "notes"
    ordinary_race_attachments = ordinary_race / "attachments"
    ordinary_url = "https://www.instagram.com/p/NOTE_RACE/"
    ordinary_results = []
    ordinary_errors = []
    def ordinary_worker():
        try:
            ordinary_results.append(kakera.save(
                ordinary_url, None, ordinary_race_notes, ordinary_race_attachments
            ))
        except Exception as error:
            ordinary_errors.append(error)
    ordinary_threads = [
        Thread(target=ordinary_worker)
        for _ in range(2)
    ]
    with patch.object(kakera.subprocess, "run", side_effect=fake_composition_download):
        for thread in ordinary_threads:
            thread.start()
        for thread in ordinary_threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
    assert not ordinary_errors and len(ordinary_results) == 2 and all(result[0] for result in ordinary_results)
    assert len(list(ordinary_race_notes.glob("*.md"))) == 1
    assert not list(ordinary_race_notes.glob("*.tmp"))

    ordinary_diff = root / "ordinary-different-note-race"
    ordinary_diff_barrier = Barrier(2)
    ordinary_diff_calls = [0]
    ordinary_diff_lock = Lock()
    def ordinary_diff_fetch(url, _browser, _account, _twitter_account, directory, name):
        ordinary_diff_barrier.wait(timeout=5)
        with ordinary_diff_lock:
            ordinary_diff_calls[0] += 1
            marker = "A" if ordinary_diff_calls[0] == 1 else "B"
        Path(directory).mkdir(parents=True, exist_ok=True)
        target = Path(directory) / "ordinary.jpg"
        target.write_bytes(b"\xff\xd8\xff\xe0ordinary-" + marker.encode())
        return {
            "name": name, "service": "instagram", "url": url,
            "metadata": {"description": f"stable title\nvariant-{marker}", "post_url": url},
            "valid": [(target, ".jpg")],
        }, None
    ordinary_diff_results, ordinary_diff_errors = [], []
    def ordinary_diff_worker():
        try:
            ordinary_diff_results.append(kakera.save(
                ordinary_url, None, ordinary_diff / "notes", ordinary_diff / "attachments"
            ))
        except Exception as error:
            ordinary_diff_errors.append(error)
    with patch.object(kakera, "fetch_source", side_effect=ordinary_diff_fetch):
        threads = [Thread(target=ordinary_diff_worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
    assert not ordinary_diff_errors and len(ordinary_diff_results) == 2
    assert all(result[0] for result in ordinary_diff_results)
    diff_notes = list((ordinary_diff / "notes").glob("*.md"))
    assert len(diff_notes) == 1 and not list((ordinary_diff / "notes").glob("*.tmp"))
    diff_text = diff_notes[0].read_text()
    assert ("variant-A" in diff_text) != ("variant-B" in diff_text)

    note_lock_root = root / "instrumented-note-lock"
    original_atomic_note = kakera.write_atomic_note
    original_capture_lock_for_notes = kakera.capture_lock
    first_entered = Event()
    second_attempting_note_lock = Event()
    release_first = Event()
    active_lock = Lock()
    active_count, max_active, atomic_calls = [0], [0], [0]
    note_lock_attempts = [0]
    def instrumented_atomic_note(note, content):
        with active_lock:
            atomic_calls[0] += 1
            active_count[0] += 1
            max_active[0] = max(max_active[0], active_count[0])
            first = atomic_calls[0] == 1
        if first:
            first_entered.set()
            assert second_attempting_note_lock.wait(timeout=5)
            assert release_first.wait(timeout=5)
        try:
            return original_atomic_note(note, content)
        finally:
            with active_lock:
                active_count[0] -= 1
    instrumented_results, instrumented_errors = [], []
    @contextmanager
    def instrumented_capture_lock(path, service, name):
        if Path(path) == note_lock_root / "notes":
            with active_lock:
                note_lock_attempts[0] += 1
                second = note_lock_attempts[0] == 2
            if second:
                second_attempting_note_lock.set()
        with original_capture_lock_for_notes(path, service, name):
            yield
    def instrumented_worker():
        try:
            instrumented_results.append(kakera.save(
                ordinary_url, None, note_lock_root / "notes", note_lock_root / "attachments"
            ))
        except Exception as error:
            instrumented_errors.append(error)
    with (
        patch.object(kakera.subprocess, "run", side_effect=fake_composition_download),
        patch.object(kakera, "write_atomic_note", side_effect=instrumented_atomic_note),
        patch.object(kakera, "capture_lock", side_effect=instrumented_capture_lock),
    ):
        first_thread = Thread(target=instrumented_worker)
        first_thread.start()
        assert first_entered.wait(timeout=5)
        second_thread = Thread(target=instrumented_worker)
        second_thread.start()
        assert second_attempting_note_lock.wait(timeout=5)
        release_first.set()
        first_thread.join(timeout=10)
        second_thread.join(timeout=10)
        assert not first_thread.is_alive() and not second_thread.is_alive()
    assert not instrumented_errors and len(instrumented_results) == 2
    assert all(result[0] for result in instrumented_results) and max_active[0] == 1
    instrumented_notes = list((note_lock_root / "notes").glob("*.md"))
    assert len(instrumented_notes) == 1 and not list((note_lock_root / "notes").glob("*.tmp"))

    composed_lock_root = root / "instrumented-composed-lock"
    composed_results, composed_errors = [], []
    second_attempting_note_lock = Event()
    original_capture_lock_for_composed = kakera.capture_lock
    note_lock_attempts = [0]
    @contextmanager
    def instrumented_composed_capture_lock(path, service, name):
        if Path(path) == composed_lock_root / "notes":
            with active_lock:
                note_lock_attempts[0] += 1
                second = note_lock_attempts[0] == 2
            if second:
                second_attempting_note_lock.set()
        with original_capture_lock_for_composed(path, service, name):
            yield
    def instrumented_composed_worker(urls):
        try:
            composed_results.append(kakera.save_composed(
                urls, None, composed_lock_root / "notes", composed_lock_root / "attachments"
            ))
        except Exception as error:
            composed_errors.append(error)
    composed_threads = [
        Thread(target=instrumented_composed_worker, args=(
            [ordinary_url, "https://www.instagram.com/p/NOTE_ADDITIONAL_A/"],
        )),
        Thread(target=instrumented_composed_worker, args=(
            [ordinary_url, "https://www.instagram.com/p/NOTE_ADDITIONAL_B/"],
        )),
    ]
    first_entered.clear()
    release_first.clear()
    active_count[0], max_active[0], atomic_calls[0] = 0, 0, 0
    with (
        patch.object(kakera.subprocess, "run", side_effect=fake_composition_download),
        patch.object(kakera, "write_atomic_note", side_effect=instrumented_atomic_note),
        patch.object(kakera, "capture_lock", side_effect=instrumented_composed_capture_lock),
    ):
        composed_threads[0].start()
        assert first_entered.wait(timeout=5)
        composed_threads[1].start()
        assert second_attempting_note_lock.wait(timeout=5)
        release_first.set()
        for thread in composed_threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
    assert not composed_errors and len(composed_results) == 2 and all(result[0] for result in composed_results)
    assert max_active[0] == 1
    composed_lock_notes = list((composed_lock_root / "notes").glob("*.md"))
    assert len(composed_lock_notes) == 1 and not list((composed_lock_root / "notes").glob("*.tmp"))

    composed_race = root / "composed-note-race"
    composed_urls = [
        "https://www.instagram.com/p/NOTE_PRIMARY/",
        "https://www.instagram.com/p/NOTE_ADDITIONAL/",
    ]
    composed_threads = [
        Thread(target=kakera.save_composed,
               args=(composed_urls, None, composed_race / "notes", composed_race / "attachments"))
        for _ in range(2)
    ]
    with patch.object(kakera.subprocess, "run", side_effect=fake_composition_download):
        for thread in composed_threads:
            thread.start()
        for thread in composed_threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
    assert len(list((composed_race / "notes").glob("*.md"))) == 1
    assert not list((composed_race / "notes").glob("*.tmp"))

    adversarial_race = root / "adversarial-note-race"
    race_barrier = Barrier(2)
    race_calls = [0]
    race_call_lock = Lock()
    def race_fetch(url, _browser, _account, _twitter_account, directory, name):
        if "PRIMARY" in url:
            race_barrier.wait(timeout=5)
        marker = "A" if "ADDITIONAL_A" in url else "B" if "ADDITIONAL_B" in url else "P"
        with race_call_lock:
            race_calls[0] += 1
        Path(directory).mkdir(parents=True, exist_ok=True)
        target = Path(directory) / f"{marker}.jpg"
        target.write_bytes(b"\xff\xd8\xff\xe0" + marker.encode() + url.encode())
        return {
            "name": name,
            "service": "instagram",
            "url": url,
            "metadata": {"description": f"body-{marker}", "post_url": url},
            "valid": [(target, ".jpg")],
        }, None
    race_inputs = [
        ["https://www.instagram.com/p/PRIMARY/", "https://www.instagram.com/p/ADDITIONAL_A/"],
        ["https://www.instagram.com/p/PRIMARY/", "https://www.instagram.com/p/ADDITIONAL_B/"],
    ]
    race_results, race_errors = [], []
    def composed_worker(urls):
        try:
            race_results.append(kakera.save_composed(
                urls, None, adversarial_race / "notes", adversarial_race / "attachments"
            ))
        except Exception as error:
            race_errors.append(error)
    with patch.object(kakera, "fetch_source", side_effect=race_fetch):
        threads = [Thread(target=composed_worker, args=(urls,)) for urls in race_inputs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
    assert not race_errors and len(race_results) == 2 and all(result[0] for result in race_results)
    race_notes = list((adversarial_race / "notes").glob("*.md"))
    assert len(race_notes) == 1 and not list((adversarial_race / "notes").glob("*.tmp"))
    race_text = race_notes[0].read_text()
    assert ("body-A" in race_text) != ("body-B" in race_text)

    retry_inbox = root / "retry.md"
    retry_inbox.write_text("- [ ] https://x.com/a/status/3\n")
    completed = set()
    real_mark_completed_group = kakera.mark_completed_group
    attempts = [0]
    def delayed_mark(inbox_path, group):
        attempts[0] += 1
        return True if attempts[0] == 1 else real_mark_completed_group(inbox_path, group)
    with (
        patch.object(kakera, "save", return_value=(True, "saved")),
        patch.object(kakera, "mark_completed_group", side_effect=delayed_mark),
    ):
        assert process_inbox(retry_inbox, None, root / "n", root / "a", completed=completed) == 1
        assert process_inbox(retry_inbox, None, root / "n", root / "a", completed=completed) == 0
    assert "[x]" in retry_inbox.read_text() and not completed

    concurrent = root / "concurrent.md"
    concurrent.write_text("- [ ] https://x.com/a/status/4\n")
    inserted = [False]
    def save_and_insert(url, *_arguments):
        if not inserted[0]:
            inserted[0] = True
            concurrent.write_text(concurrent.read_text() + "- [ ] https://x.com/a/status/4\n")
        return True, "saved"
    with patch.object(kakera, "save", side_effect=save_and_insert) as save:
        assert process_inbox(concurrent, None, root / "n", root / "a") == 0
    assert save.call_count == 1
    assert concurrent.read_text().count("[x]") == 2

    tracking = root / "tracking.md"
    tracking.write_text(
        "- [ ] https://x.com/a/status/5?utm_source=one\n"
        "- [ ] https://x.com/a/status/5/\n"
    )
    with patch.object(kakera, "save", return_value=(True, "saved")) as save:
        assert process_inbox(tracking, None, root / "n", root / "a") == 0
    assert save.call_count == 1 and tracking.read_text().count("[x]") == 2

    hierarchy_config = root / "todoist-hierarchy.json"
    hierarchy_config.write_text(json.dumps({"todoist": {"project_id": "p"}}))
    hierarchy_calls = []
    def hierarchy_urlopen(request, **_arguments):
        hierarchy_calls.append((request.method, request.full_url))
        if request.method == "POST":
            return TodoistResponse(b"null")
        return TodoistResponse(json.dumps({"results": [
            {"id": "root", "parent_id": None, "child_order": 0,
             "content": "https://x.com/a/status/root", "labels": ["Root", "Duplicate"]},
            {"id": "child-late", "parent_id": "root", "child_order": 2,
             "content": "https://x.com/a/status/late-content",
             "description": "https://x.com/a/status/late-description",
             "labels": ["Late", "root"]},
            {"id": "child-early", "parent_id": "root", "child_order": 1,
             "content": "https://x.com/a/status/early-content", "labels": ["Early"]},
            {"id": "grandchild", "parent_id": "child-early", "child_order": 1,
             "content": "https://x.com/a/status/grandchild", "labels": ["Grand"]},
        ], "next_cursor": None}).encode())
    with (
        patch.object(kakera, "CONFIG", hierarchy_config),
        patch.object(kakera, "urlopen", side_effect=hierarchy_urlopen),
        patch.dict(kakera.os.environ, {"TODOIST_API_TOKEN": "secret"}),
        patch.object(kakera, "save_composed", return_value=(True, "saved")) as composed,
    ):
        assert process_todoist(None, root / "n", root / "a", tags=["CLI"]) == 0
    assert composed.call_args.args[0] == [
        "https://x.com/a/status/root",
        "https://x.com/a/status/early-content",
        "https://x.com/a/status/grandchild",
        "https://x.com/a/status/late-content",
        "https://x.com/a/status/late-description",
    ]
    assert hierarchy_calls[-1][0] == "POST" and "/tasks/root/close" in hierarchy_calls[-1][1]
    assert not any("child-" in url for method, url in hierarchy_calls if method == "POST")
    assert composed.call_args.args[-1] == ["Root", "Duplicate", "Early", "Grand", "Late", "CLI"]

    hierarchy_calls.clear()
    with (
        patch.object(kakera, "CONFIG", hierarchy_config),
        patch.object(kakera, "urlopen", side_effect=hierarchy_urlopen),
        patch.dict(kakera.os.environ, {"TODOIST_API_TOKEN": "secret"}),
        patch.object(kakera, "save_composed", return_value=(True, "partial saved")),
    ):
        assert process_todoist(None, root / "n", root / "a") == 0
    assert [url for method, url in hierarchy_calls if method == "POST"] == [
        "https://api.todoist.com/api/v1/tasks/root/close"
    ]

    hierarchy_calls.clear()
    with (
        patch.object(kakera, "CONFIG", hierarchy_config),
        patch.object(kakera, "urlopen", side_effect=hierarchy_urlopen),
        patch.dict(kakera.os.environ, {"TODOIST_API_TOKEN": "secret"}),
        patch.object(kakera, "save_composed", return_value=(False, "no images")),
    ):
        assert process_todoist(None, root / "n", root / "a") == 1
    assert not [url for method, url in hierarchy_calls if method == "POST"]


with TemporaryDirectory() as directory:
    root = Path(directory)
    vault, notes = root / "vault", root / "vault" / "Kakera"
    notes.mkdir(parents=True)
    image = vault / "photo.jpg"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"\xff\xd8\xffimage")
    note = notes / "Folder" / "Example.md"
    note.parent.mkdir()
    note.write_text(
        '---\nsource: "https://example.test/source"\n---\n'
        '![[photo.jpg]]\n'
    )
    matches, selected = kakera._telegram_note_candidates(
        "https://example.test/source", notes, vault
    )
    assert matches == [note.resolve()] and selected == "https://example.test/source"
    assert kakera._telegram_note_candidates("[[Folder/Example]]", notes, vault)[0] == [note.resolve()]
    assert kakera._telegram_note_images(note, vault) == [image.resolve()]
    assert kakera._telegram_note_caption(Path("a" * 1100 + ".md"), "x") == "a" * 1024
    assert kakera._set_telegram_receipt(
        "---\nsource: x\n---\nbody\n", "-1", [4, 5]
    ) == "---\nsource: x\nkakera: {\"shared\":{\"telegram\":{\"-1\":[4,5]}}}\n---\nbody\n"

    # Telegram's shared seams: multipart safety, ambiguity, concurrent writeback,
    # and the configured-vault boundary.
    odd = vault / "odd\n\"name.jpg"
    odd.write_bytes(b"\xff\xd8\xffodd")
    body, boundary = kakera._telegram_multipart("sendPhoto", "-1", "caption", [odd])
    assert b'filename="photo.jpg"' in body and b'odd\n"name.jpg' not in body
    duplicate = vault / "other" / "photo.jpg"
    duplicate.parent.mkdir()
    duplicate.write_bytes(b"\xff\xd8\xffduplicate")
    bare = notes / "bare.md"
    bare.write_text("![[photo.jpg]]\n")
    assert kakera._telegram_note_images(bare, vault, kakera._telegram_image_index(vault)) == []
    limited = []
    for index in range(13):
        path = vault / f"limit-{index}.jpg"
        path.write_bytes(b"\xff\xd8\xfflimit")
        if index == 0:
            path.open("ab").truncate(kakera.TELEGRAM_MAX_BYTES + 1)
        limited.append(path)
    limited_note = notes / "limited.md"
    limited_note.write_text("\n".join(f"![[{path.name}]]" for path in limited) + "\n")
    warning = io.StringIO()
    with redirect_stderr(warning):
        selected = kakera._telegram_note_images(
            limited_note, vault, kakera._telegram_image_index(vault)
        )
    assert selected == [path.resolve() for path in limited[1:11]]
    assert "skipped 1 Telegram image(s) over 10 MB" in warning.getvalue()
    assert "skipped 2 eligible Telegram image(s) after the first 10" in warning.getvalue()

    config = root / "telegram.json"
    config.write_text(json.dumps({
        "obsidian": {"vault": str(vault), "notes": "Kakera", "attachments": "attachments"},
        "telegram": {"chat_id": "-1"},
    }))
    with patch.object(kakera, "CONFIG", config), patch.dict(kakera.os.environ, {"TELEGRAM_BOT_TOKEN": "secret"}):
        root_note = notes / "Example.md"
        root_note.write_text("![[photo.jpg]]\n")
        indexed_note = notes / "Indexed.md"
        indexed_source = "https://www.instagram.com/p/INDEXED/?foo=bar#comments"
        indexed_base = "https://www.instagram.com/p/INDEXED/"
        indexed_note.write_text(
            f'---\ninstagram: "{indexed_source}"\n---\n![[photo.jpg]]\n'
        )
        indexed_matches, indexed_selected = kakera._telegram_note_candidates(
            indexed_source, notes, vault
        )
        assert indexed_matches == [indexed_note.resolve()]
        assert indexed_selected == indexed_base
        assert kakera._telegram_note_caption(indexed_note, indexed_note.read_text()).endswith(indexed_base)
        with patch.object(kakera, "telegram_send") as send:
            assert kakera.telegram_command(["[[Example]]"]) == 1
            assert kakera.telegram_command(["[[Example.md]]"]) == 1
            assert not send.called

        leading_config = root / "leading.json"
        leading_config.write_text(json.dumps({
            "obsidian": {"vault": str(vault), "notes": "Kakera", "attachments": "attachments"},
            "telegram": {"chat_id": "00123"},
        }))
        with patch.object(kakera, "CONFIG", leading_config):
            assert kakera.configured_telegram_chat_id() == "123"
            assert kakera._telegram_receipt(
                '---\nkakera: {"shared":{"telegram":{"123":[1]}}}\n---\n', "00123"
            )[1] == [1]
            for bad in (True, "12x", ""):
                try:
                    kakera.canonical_telegram_chat_id(bad)
                except ValueError:
                    pass
                else:
                    raise AssertionError("accepted invalid Telegram chat ID")

        current = notes / "concurrent.md"
        current.write_text("![](../photo.jpg)\noriginal\n")
        def fake_send(*_arguments):
            current.write_text("![](../photo.jpg)\nconcurrent edit\n")
            return [99]
        with patch.object(kakera, "telegram_send", side_effect=fake_send):
            assert kakera.publish_telegram_note(current, vault) == (
                True, "Telegram sent 1 image(s) to -1"
            )
        assert "concurrent edit" in current.read_text() and '"-1":[99]' in current.read_text()
        with patch.object(kakera, "telegram_send") as send:
            assert kakera.publish_telegram_note(current, vault) == (
                True, "Telegram already sent to -1"
            )
            assert not send.called

        duplicate_receipt = notes / "duplicate-receipt.md"
        duplicate_receipt.write_text(
            '---\nkakera: {"shared":{"telegram":{"-1":[1,"x"]}}}\n'
            'kakera: {"shared":{"telegram":{"-1":[2]}}}\n---\n![](../photo.jpg)\n'
        )
        with patch.object(kakera, "telegram_send") as send:
            try:
                kakera.publish_telegram_note(duplicate_receipt, vault)
            except ValueError as error:
                assert "duplicate" in str(error)
            else:
                raise AssertionError("accepted duplicate Telegram receipt")
            assert not send.called

        outside = root / "outside-notes"
        outside.mkdir()
        link = vault / "linked-notes"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pass
        else:
            escaped = root / "escaped.json"
            escaped.write_text(json.dumps({
                "obsidian": {"vault": str(vault), "notes": "linked-notes", "attachments": "attachments"},
                "telegram": {"chat_id": "-1"},
            }))
            with patch.object(kakera, "CONFIG", escaped):
                try:
                    kakera.telegram_command(["anything"])
                except ValueError as error:
                    assert "inside the vault" in str(error)
                else:
                    raise AssertionError("accepted notes folder symlink outside vault")

        class TelegramResponse:
            def __init__(self, body):
                self.body = body
            def __enter__(self):
                return self
            def __exit__(self, *_arguments):
                return False
            def read(self):
                return self.body

        requests = []
        with patch.object(
            kakera, "urlopen",
            side_effect=lambda request, **_kwargs: (
                requests.append(request) or TelegramResponse(
                    b'{"ok":true,"result":[{"message_id":1},{"message_id":2}]}'
                )
            ),
        ):
            assert kakera.telegram_send("-1", "caption", [image, duplicate]) == [1, 2]
        assert "sendMediaGroup" in requests[0].full_url
        assert b'filename="file0.jpg"' in requests[0].data
        for malformed in (
            b'{"ok":true,"result":[]}',
            b'{"ok":true,"result":{"message_id":true}}',
            b'{"ok":true,"result":{"message_id":0}}',
        ):
            with patch.object(kakera, "urlopen", return_value=TelegramResponse(malformed)):
                try:
                    kakera.telegram_send("-1", "caption", [image])
                except ValueError:
                    pass
                else:
                    raise AssertionError("accepted malformed Telegram message response")
        with patch.object(
            kakera, "urlopen",
            return_value=TelegramResponse(b'{"ok":true,"result":[{"message_id":1}]}'),
        ):
            try:
                kakera.telegram_send("-1", "caption", [image, duplicate])
            except ValueError:
                pass
            else:
                raise AssertionError("accepted Telegram media count mismatch")
        with patch.object(kakera, "urlopen", side_effect=OSError("secret token in URL")):
            try:
                kakera.telegram_send("-1", "caption", [image])
            except ValueError as error:
                assert "secret" not in str(error)
            else:
                raise AssertionError("Telegram transport failure was accepted")

        resend = notes / "resend.md"
        resend.write_text('---\nkakera: {"shared":{"telegram":{"-1":[3]}}}\n---\n![](../photo.jpg)\n')
        with patch.object(kakera.sys, "stdin", SimpleNamespace(isatty=lambda: False)):
            try:
                kakera.publish_telegram_note(resend, vault, manual=True)
            except ValueError as error:
                assert "interactive" in str(error)
            else:
                raise AssertionError("accepted non-interactive resend")
        with patch.object(kakera.sys, "stdin", SimpleNamespace(isatty=lambda: True, readline=lambda: "n\n")), \
             patch.object(kakera, "telegram_send") as send:
            assert kakera.publish_telegram_note(resend, vault, manual=True) == (True, "resend declined")
            assert not send.called
        with patch.object(kakera.sys, "stdin", SimpleNamespace(isatty=lambda: True, readline=lambda: "yes\n")), \
             patch.object(kakera, "telegram_send", return_value=[4]):
            assert kakera.publish_telegram_note(resend, vault, manual=True) == (
                True, "Telegram sent 1 image(s) to -1"
            )
        with patch.object(kakera.sys, "stdin", SimpleNamespace(isatty=lambda: True, readline=lambda: "\n")):
            assert kakera.publish_telegram_note(resend, vault, manual=True) == (True, "resend declined")
        with patch.object(kakera.sys, "stdin", SimpleNamespace(isatty=lambda: True, readline=lambda: "")):
            try:
                kakera.publish_telegram_note(resend, vault, manual=True)
            except ValueError as error:
                assert "end of input" in str(error)
            else:
                raise AssertionError("accepted EOF resend confirmation")
        with patch.object(kakera.sys, "stdin", SimpleNamespace(
            isatty=lambda: True, readline=lambda: (_ for _ in ()).throw(KeyboardInterrupt)
        )):
            assert kakera.telegram_command(["resend", "missing"]) == 130
        failed_note = notes / "failed.md"
        failed_note.write_text("![](../photo.jpg)\n")
        original = failed_note.read_text()
        with patch.object(kakera, "telegram_send", side_effect=ValueError("down")):
            failed_delivery = kakera.publish_telegram_note(failed_note, vault)
        assert failed_delivery == (
            False, "Telegram delivery uncertain; check the configured chat before retrying"
        )
        assert failed_note.read_text() == original


with TemporaryDirectory() as directory:
    root = Path(directory)
    notes, attachments = root / "notes", root / "attachments"
    notes.mkdir()
    help_output = io.StringIO()
    with patch.object(kakera.sys, "argv", ["kakera.py", "--help"]), redirect_stdout(help_output):
        try:
            kakera.main()
        except SystemExit as error:
            assert error.code == 0
    help_text = " ".join(help_output.getvalue().split())
    for phrase in (
        "kakera --telegram URL [URL ...]",
        "kakera telegram SELECTOR [SELECTOR ...]",
        "telegram-only SELECTOR [SELECTOR ...]",
        "kakera --telegram-only URL [URL ...]",
        "--tag share/telegram",
        "save the Capture first",
        "no durable Kakera output",
        "URL, or note selector",
    ):
        assert phrase in help_text
    with (
        patch.object(kakera, "configured_folders", return_value=(notes, attachments)),
        patch.object(kakera, "save", return_value=(True, "saved")) as save,
        patch.object(kakera.sys, "argv", ["kakera.py", "--telegram", "one"]),
    ):
        assert kakera.main() == 0
    assert save.call_args.args[-1] == ["share/telegram"]

    with (
        patch.object(kakera, "configured_folders", side_effect=AssertionError("local used config")),
        patch.object(kakera, "save", return_value=(True, "saved")) as save,
        patch.object(kakera.sys, "argv", ["kakera.py", "--local", "--telegram", "one"]),
    ):
        assert kakera.main() == 0
    assert save.call_args.args[2:4] == (kakera.ROOT / "downloads", kakera.ROOT / "attachments")

    for flag, selector in (("--telegram-note", "local"), ("--telegram-note-only", "inbox")):
        with patch.object(kakera.sys, "argv", ["kakera.py", flag, selector]), \
             patch.object(kakera, "telegram_command", return_value=0) as note_command, \
             patch.object(kakera, "telegram_note_only_command", return_value=0) as note_only_command:
            assert kakera.main() == 0
        (note_command if flag == "--telegram-note" else note_only_command).assert_called_once_with([selector])

    with patch.object(kakera.sys, "argv", ["kakera.py", "--telegram-only"]), \
         patch.object(kakera, "configured_telegram_chat_id", side_effect=AssertionError("config before URL")), \
         patch.object(kakera, "telegram_token", side_effect=AssertionError("token before URL")):
        try:
            kakera.main()
        except SystemExit as error:
            assert error.code == 2
        else:
            raise AssertionError("accepted --telegram-only without a URL")

    queue = root / "inbox.md"
    queue.write_text("- [ ] https://x.com/a/status/queue\n")
    with patch.object(kakera, "save", return_value=(False, "capture saved; Telegram failed: down")):
        assert kakera.process_inbox(queue, None, notes, attachments, tags=["share/telegram"]) == 1
    assert "[ ]" in queue.read_text()

    for argv, function_name in (
        (["kakera.py", "--telegram", "--instagram-cookies", "x"], "instagram_cookies"),
        (["kakera.py", "--telegram", "--reddit-oauth", "x"], "reddit_oauth"),
        (["kakera.py", "--telegram-note", "--watch", "Note.md"], "telegram_command"),
        (["kakera.py", "--telegram-only", "--compose", "https://example.test/x"], "telegram_only_url"),
    ):
        with patch.object(kakera.sys, "argv", argv), patch.object(kakera, function_name) as called:
            try:
                kakera.main()
            except SystemExit as error:
                assert error.code == 2
            else:
                raise AssertionError("accepted conflicting Telegram grammar")
            assert not called.called

    todo_config = root / "todo.json"
    todo_config.write_text(json.dumps({"todoist": {"project_id": "p"}}))
    task = {"id": "task", "content": "https://x.com/a/status/todo", "labels": []}
    with (
        patch.object(kakera, "CONFIG", todo_config),
        patch.dict(kakera.os.environ, {"TODOIST_API_TOKEN": "token"}),
        patch.object(kakera, "todoist_task_pages", return_value=iter([[task]])),
        patch.object(kakera, "todoist_request", return_value={}),
        patch.object(kakera, "save", return_value=(True, "saved")) as save,
    ):
        assert kakera.process_todoist(None, notes, attachments, tags=["share/telegram"]) == 0
    assert save.call_args.args[-1] == ["share/telegram"]

    watched = root / "watched.md"
    watched.write_text("# Inbox\n")
    with patch.object(kakera, "process_inbox", return_value=0) as process, \
         patch.object(kakera.time, "sleep", side_effect=KeyboardInterrupt):
        assert kakera.watch_inbox(watched, None, notes, attachments, tags=["share/telegram"]) == 0
    assert process.call_args.args[-1] == ["share/telegram"]


with TemporaryDirectory() as directory:
    root = Path(directory)
    vault, notes, attachments = root / "vault", root / "vault" / "notes", root / "vault" / "attachments"
    notes.mkdir(parents=True)
    attachments.mkdir()
    incoming = root / "incoming.jpg"
    incoming.write_bytes(b"\xff\xd8\xffincoming")
    config = root / "telegram-capture.json"
    config.write_text(json.dumps({
        "obsidian": {"vault": str(vault), "notes": "notes", "attachments": "attachments"},
        "telegram": {"chat_id": "-1"},
    }))

    def fake_fetch(url, _browser, _account, _twitter_account, _directory, name):
        return ({"name": name, "service": "instagram", "url": url,
                 "metadata": {"title": "Capture", "post_url": url},
                 "valid": [(incoming, ".jpg")]}, None)

    stored_image = attachments / "capture.jpg"
    stored_image.write_bytes(incoming.read_bytes())
    transport_calls = []

    def fake_store(source, _notes, _attachments):
        source["images"] = [stored_image]
        return [stored_image]

    with (
        patch.object(kakera, "CONFIG", config),
        patch.object(kakera, "fetch_source", side_effect=fake_fetch),
        patch.object(kakera, "store_source_images", side_effect=fake_store),
        patch.object(kakera, "telegram_send", side_effect=lambda *args: (transport_calls.append(args) or [7])),
        patch.dict(kakera.os.environ, {"TELEGRAM_BOT_TOKEN": "token"}),
    ):
        success, message = kakera.save(
            "https://instagram.com/p/CAPTURE", None, notes, attachments, tags=["share/telegram"]
        )
        assert success and "Telegram sent 1 image(s) to -1" in message
        assert len(transport_calls) == 1
        success, message = kakera.save(
            "https://instagram.com/p/CAPTURE", None, notes, attachments, tags=["share/telegram"]
        )
        assert success and "Telegram already sent to -1" in message
        assert len(transport_calls) == 1

        old_url = "https://instagram.com/p/OLD"
        old_note = notes / "Old.md"
        kakera.write_note(old_note, old_url, [stored_image],
                          {"title": "Old", "post_url": old_url}, "instagram", ["share/telegram"])
        success, message = kakera.save(old_url, None, notes, attachments)
        assert success and "Telegram" not in message
        assert len(transport_calls) == 1
        legacy_note = notes / "Legacy.md"
        kakera.write_note(legacy_note, "https://instagram.com/p/LEGACY", [stored_image],
                          {"title": "Legacy", "post_url": "https://instagram.com/p/LEGACY"},
                          "instagram", ["to/telegram"])
        legacy_success, legacy_message = kakera.save(
            "https://instagram.com/p/LEGACY", None, notes, attachments
        )
        assert legacy_success and "Telegram" not in legacy_message
        assert len(transport_calls) == 1

        failed_url = "https://instagram.com/p/FAILED"
        with patch.object(kakera, "_write_telegram_receipt", side_effect=OSError("read-only")):
            success, message = kakera.save(
                failed_url, None, notes, attachments, tags=["share/telegram"]
            )
        assert not success and message.startswith("capture saved; Telegram failed:")
        transport_failed_url = "https://instagram.com/p/TRANSPORT-FAILED"
        with patch.object(kakera, "telegram_send", side_effect=ValueError("timeout")):
            success, message = kakera.save(
                transport_failed_url, None, notes, attachments, tags=["share/telegram"]
            )
        assert not success and message == (
            "capture saved; Telegram failed: "
            "Telegram delivery uncertain; check the configured chat before retrying"
        )

        compose_success, compose_message = kakera.save_composed(
            ["https://instagram.com/p/ONE", "https://instagram.com/p/TWO"],
            None, notes, attachments, tags=["share/telegram"]
        )
        assert compose_success and "Telegram sent 1 image(s) to -1" in compose_message


with TemporaryDirectory() as directory:
    root = Path(directory)
    vault = root / "vault"
    notes = vault / "notes"
    attachments = vault / "attachments"
    notes.mkdir(parents=True)
    attachments.mkdir()
    image = notes / "image.jpg"
    image.write_bytes(b"\xff\xd8\xffimage")
    config = root / "nested.json"
    config.write_text(json.dumps({
        "obsidian": {"vault": str(vault), "notes": "notes", "attachments": "attachments"},
        "telegram": {"chat_id": "00123"},
    }))
    note = notes / "repeat.md"
    note.write_text(
        '---\nsource: "https://example.test/repeat"\n'
        'kakera: {"keep":{"x":[1]},"shared":{"other":{"ok":true}}}\n'
        '---\n![](image.jpg)\n'
    )
    with patch.object(kakera, "CONFIG", config), patch.dict(kakera.os.environ, {"TELEGRAM_BOT_TOKEN": "token"}):
        assert kakera._telegram_receipt(note.read_text(), "123")[1] is None
        updated = kakera._set_telegram_receipt(note.read_text(), "00123", [7])
        assert '"keep":{"x":[1]}' in updated and '"other":{"ok":true}' in updated
        assert '"123":[7]' in updated
        old = '---\nto_telegram: {"123":[99]}\n---\n![](image.jpg)\n'
        assert kakera._telegram_receipt(old, "123")[1] is None
        nested_only = '---\nmeta:\n  kakera: {"shared":{"telegram":{"123":[9]}}}\n---\nbody\n'
        assert kakera._telegram_receipt(nested_only, "123")[1] is None
        nested_updated = kakera._set_telegram_receipt(nested_only, "123", [10])
        assert '  kakera: {"shared":{"telegram":{"123":[9]}}}' in nested_updated
        assert nested_updated.endswith("body\n")
        top_and_nested = (
            '---\nmeta:\n  kakera: {"keep":true}\n'
            'kakera: {"shared":{"telegram":{"123":[9]}}}\n---\nbody\n'
        )
        top_updated = kakera._set_telegram_receipt(top_and_nested, "123", [10])
        assert '  kakera: {"keep":true}' in top_updated and '"123":[10]' in top_updated
        whitespace_only = '---\n kakera: {"shared":{}}\nkakera : {"shared":{}}\n---\nbody\n'
        whitespace_updated = kakera._set_telegram_receipt(whitespace_only, "123", [11])
        assert whitespace_updated.count('kakera: {"shared":{"telegram":{"123":[11]}}}') == 1
        assert ' kakera: {"shared":{}}' in whitespace_updated
        assert 'kakera : {"shared":{}}' not in whitespace_updated
        preserved_note = notes / "preserved.md"
        preserved_note.write_text('---\nkakera: {"unknown":{"value":[1,true]}}\n---\nold\n')
        kakera.write_note(preserved_note, "https://example.test/preserved", [image],
                          {"title": "Preserved", "post_url": "https://example.test/preserved"},
                          "instagram", [])
        assert '"unknown":{"value":[1,true]}' in preserved_note.read_text()
        composed_note = notes / "composed.md"
        composed_note.write_text('---\nkakera: {"unknown":{"nested":{"ok":true}}}\n---\nold\n')
        kakera.write_composed_note(composed_note, [{
            "service": "instagram", "url": "https://example.test/composed",
            "metadata": {"title": "Composed", "post_url": "https://example.test/composed"},
            "images": [image],
        }], [])
        assert '"unknown":{"nested":{"ok":true}}' in composed_note.read_text()
        for malformed in (
            '---\nkakera: null\n---\n',
            '---\nkakera: {"shared":null}\n---\n',
            '---\nkakera: {"unknown":NaN}\n---\n',
            '---\nkakera: {"shared":{"other":{"value":Infinity}}}\n---\n',
            '---\nkakera: {"shared":{}}\nkakera : {"shared":{}}\n---\n',
            '---\nkakera: {"shared":{"telegram":{"00123":[1],"123":[2]}}}\n---\n',
            '---\nkakera: {"shared":{"telegram":{"123":[1, true]}}}\n---\n',
            '---\nkakera: {"shared":{"telegram":{"123":[1]}}}\n'
            'kakera: {"shared":{"telegram":{"123":[2]}}}\n---\n',
        ):
            try:
                kakera._telegram_receipt(malformed, "123")
            except ValueError:
                pass
            else:
                raise AssertionError("accepted malformed nested Telegram receipt")

        before = note.read_text()
        sends = []
        with patch.object(kakera, "telegram_send", side_effect=lambda *args: (sends.append(args) or [8])):
            assert kakera.telegram_note_only_command(["repeat", "repeat"]) == 0
        assert len(sends) == 2 and note.read_text() == before
        note_mtime = note.stat().st_mtime_ns
        with patch.object(kakera, "fetch_source") as fetch:
            assert kakera.telegram_note_only_command(["missing-note"]) == 1
        assert note.stat().st_mtime_ns == note_mtime and not fetch.called

        transient_url = "https://instagram.com/p/TRANSIENT?foo=bar&igsh=tracking#comments"
        output_roots = {root / "downloads", root / "attachments"}
        observed = []
        def fake_transient_fetch(_url, _browser, _account, _twitter, directory, name):
            path = directory / "source.jpg"
            path.write_bytes(b"\xff\xd8\xfftemporary")
            return ({"name": name, "service": "instagram", "url": _url,
                     "metadata": {"title": "Transient", "post_url": _url},
                     "valid": [(path, ".jpg")]}, None)
        def observe_send(_chat, caption, images, _token):
            observed.append((images[0].exists(), images[0].parent, caption))
            return [10]
        with patch.object(kakera, "fetch_source", side_effect=fake_transient_fetch), \
             patch.object(kakera, "telegram_send", side_effect=observe_send):
            success, message = kakera.telegram_only_url(transient_url, None)
        assert success and "nothing saved" in message and observed and observed[0][0]
        assert observed[0][2].endswith("\nhttps://instagram.com/p/TRANSIENT/")
        assert not any(path.exists() for path in output_roots)
        assert not observed[0][1].exists()
        created = []
        def failing_fetch(_url, _browser, _account, _twitter, directory, name):
            created.append(Path(directory))
            path = Path(directory) / "source.jpg"
            path.write_bytes(b"\xff\xd8\xfftemporary")
            return ({"name": name, "service": "instagram", "metadata": {"title": "x"},
                     "valid": [(path, ".jpg")]}, None)
        with patch.object(kakera, "fetch_source", side_effect=failing_fetch), \
             patch.object(kakera, "telegram_send", side_effect=ValueError("api")):
            success, message = kakera.telegram_only_url(transient_url, None)
        assert not success and message == "Telegram delivery uncertain; check the configured chat before retrying"
        assert created and not created[-1].exists()
        created.clear()
        def exception_fetch(_url, _browser, _account, _twitter, directory, _name):
            created.append(Path(directory))
            raise OSError("fetch")
        with patch.object(kakera, "fetch_source", side_effect=exception_fetch):
            try:
                kakera.telegram_only_url(transient_url, None)
            except OSError:
                pass
            else:
                raise AssertionError("fetch exception was accepted")
        assert created and not created[-1].exists()
        with patch.object(kakera, "fetch_source", side_effect=lambda _url, _browser, _account, _twitter, directory, name: (
                {"name": name, "service": "instagram", "metadata": {}, "valid": []}, None)):
            success, message = kakera.telegram_only_url(transient_url, None)
        assert not success and "Telegram not sent" in message
        with patch.object(kakera, "fetch_source", return_value=({"valid": [], "error": "partial"}, None)), \
             patch.object(kakera, "telegram_send") as send:
            success, message = kakera.telegram_only_url(transient_url, None)
        assert not success and "Telegram not sent" in message and not send.called
        with patch.object(kakera.sys, "argv", ["kakera.py", "--telegram-only", transient_url,
                                                  "https://instagram.com/p/SECOND"]), \
             patch.object(kakera, "telegram_only_url", side_effect=[
                 (True, "Telegram sent 1 image(s) to 123; nothing saved"),
                 (False, "Telegram not sent: failed"),
             ]) as transient_send:
            assert kakera.main() == 1
        assert transient_send.call_count == 2
        with patch.object(kakera.sys, "argv", ["kakera.py", "--telegram-only", "--browser", "safari", transient_url]), \
             patch.object(kakera, "telegram_only_url", return_value=(True, "ok")) as allowed:
            assert kakera.main() == 0
        assert allowed.call_args.args[1] == "safari"


with TemporaryDirectory() as directory:
    root = Path(directory)
    fake_bin = root / "bin"
    fake_bin.mkdir()
    args_file = root / "args"
    uv = fake_bin / "uv"
    uv.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$KAKERA_UV_ARGS\"\n")
    uv.chmod(0o755)
    environment = {**os.environ, "PATH": f"{fake_bin}:/usr/bin:/bin", "KAKERA_UV_ARGS": str(args_file)}

    def launcher(*args):
        result = subprocess.run([str(kakera.ROOT / "kakera"), *args], env=environment,
                                capture_output=True, text=True)
        return result.returncode, args_file.read_text().splitlines()

    code, argv = launcher("--telegram-only", "https://example.test/a")
    assert code == 0 and "--obsidian" not in argv and "--telegram-only" in argv
    code, argv = launcher("local", "--telegram-only", "https://example.test/a")
    assert code == 0 and "--local" in argv and "--telegram-only" in argv
    code, argv = launcher("telegram-only", "Note.md")
    assert code == 0 and "--telegram-note-only" in argv and "Note.md" in argv
    code, argv = launcher("telegram", "local")
    assert code == 0 and "--telegram-note" in argv and "local" in argv
    code, argv = launcher("telegram-only", "inbox")
    assert code == 0 and "--telegram-note-only" in argv and "inbox" in argv

    for command in (("local", "telegram-only", "https://example.test/a"),
                    ("inbox", "telegram-only", "https://example.test/a"),
                    ("telegram", "telegram-only", "Note.md")):
        result = subprocess.run(
            [os.sys.executable, str(kakera.ROOT / "kakera.py"), *command],
            capture_output=True, text=True,
        )
        assert result.returncode == 2 and "pseudo-subcommands" in result.stderr
