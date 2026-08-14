# Kakera

A personal command-line tool that saves fragments of public source posts into portable local folders.

## Language

**Capture**:
A Source Note and its Attachments, saved from one public source post. Repeated runs for the same post reuse the same Capture.
_Avoid_: Download, saved post, bookmark

**Source Note**:
The Markdown file named for the post title, stored locally in `downloads/` or in the configured Obsidian notes folder. A colliding title becomes `Title - POST_ID - Service.md`. It contains Obsidian properties, available source text, and relative links to its Attachments.
_Avoid_: Metadata file, source file

**Attachment**:
An image belonging to a Capture and stored under its Source Service in the local or configured Obsidian attachments folder.
_Avoid_: Asset, media file, downloaded image

**Inbox**:
An Obsidian note containing unchecked Markdown tasks whose URLs are waiting to become Captures. Successful tasks are checked; unsuccessful tasks remain pending.
_Avoid_: Queue database, link file

**Source Service**:
The external service from which a Capture originated. Kakera v0.1 supports Instagram, Reddit, and public RedNote posts.
_Avoid_: Platform, provider

**Canonical Source URL**:
The stable form of a source post's URL used to identify repeated runs, with only recognized tracking parameters removed. The original URL remains in the Source Note.
_Avoid_: Clean URL, normalized URL
