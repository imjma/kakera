import errno
import json
from pathlib import Path
from subprocess import CompletedProcess
from tempfile import TemporaryDirectory
from unittest.mock import patch

import kakera
from kakera import (
    canonical_url,
    capture_id,
    configured_browser,
    configured_folders,
    configured_inbox,
    configured_instagram_account,
    configured_reddit,
    instagram_cookies,
    replace_text,
    parse_rednote,
    process_inbox,
    reddit_oauth,
    write_note,
)


assert capture_id("https://www.instagram.com/p/ABC_123/?igsh=tracking") == "instagram-ABC_123"
assert capture_id("https://redd.it/1abcxyz?utm_source=share") == "reddit-1abcxyz"
assert canonical_url("https://redd.it/1abcxyz/?utm_source=x&keep=yes#top") == "https://redd.it/1abcxyz?keep=yes"
assert capture_id("http://xhslink.com/o/ABC").startswith("rednote-")

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
    assert "tags:\n  - instagram" in text
    assert "A caption\nwith body text" in text
    assert "![](../attachments/instagram/instagram-ABC_123-01.jpg)" in text

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
    inbox.write_text("- [ ] https://www.instagram.com/p/ABC/\n- [ ] http://xhslink.com/o/failed\n")
    with patch.object(kakera, "save", side_effect=((True, "saved"), (False, "failed"))):
        assert process_inbox(inbox, "orion", vault / "Clippings", vault / "assets") == 1
    assert inbox.read_text() == (
        "- [x] https://www.instagram.com/p/ABC/\n- [ ] http://xhslink.com/o/failed\n"
    )

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
