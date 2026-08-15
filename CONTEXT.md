# Kakera

A personal command-line tool that saves fragments of public source posts into portable local folders.

## Language

**Capture**:
A Source Note and referenced Attachments produced from one or more ordered submitted sources. A Capture may contain verified Source Posts and Failed Sources; Attachments can be referenced or reused by other Captures. A single-source Capture is the ordinary case; a composed Capture is the same model with additional sources.
_Avoid_: Download, saved post, bookmark

**Capture Tag**:
A user-controlled Obsidian tag on the whole Capture. Service tags are generated from the current sources; Capture Tags come from `--tag`, Inbox hashtags, Todoist labels, or existing note tags. They are normalized and deduplicated case-insensitively, and are never stored as per-source properties.
_Avoid_: Service tag, queue label

**Submitted Source**:
One URL supplied to Kakera. It becomes the Primary Source when first in a Capture, or an Additional Source when later. It is not necessarily a verified public post.
_Avoid_: Input post, link item

**Source Post**:
The verified public post resolved from a Submitted Source, including its metadata, text, images, canonical identity, and Source Service.
_Avoid_: Item, link, child capture

**Primary Source**:
The first Submitted Source in a Capture. It always supplies the Source Note's stable identity, filename, title, and primary URL; frontmatter author fields are added only when it resolves. A failed or unsupported Primary Source remains the first submitted input and records its failure in the note body.
_Avoid_: Main image, preferred source

**Additional Source**:
Any Submitted Source after the Primary Source. If it resolves to a Source Post, its metadata, text, and image links are written in an ordered Markdown section rather than duplicated in frontmatter.
_Avoid_: Secondary capture

**Failed Source**:
A Submitted Source that could not be verified or supplied no supported image. Its URL and failure reason are written in the Source Note body; it is not called a Source Post. A composition is saved when another Source Post supplies at least one image.
_Avoid_: Ignored URL, skipped source

**Source Note**:
The Markdown file named for the Primary Source title, or its stable post ID/canonical hash fallback when no title is available, stored locally in `downloads/` or in the configured Obsidian notes folder. A colliding title becomes `Title - POST_ID - Service.md`. It contains Obsidian properties, available source text, and relative links to its Attachments.
_Avoid_: Metadata file, source file

**Attachment**:
An image referenced by a Source Note and stored under its Source Service in the local or configured Obsidian attachments folder. The same file may be referenced or reused across Captures.
_Avoid_: Asset, media file, downloaded image

**Inbox**:
An Obsidian note containing unchecked Markdown tasks whose URLs are waiting to become Captures. A parent task and nested subtasks may form one ordered composition. Successful groups are checked; unsuccessful groups remain pending.
_Avoid_: Queue database, link file

**Source Service**:
The external service associated with a verified Source Post or recognized submitted source. Kakera v0.1 supports Instagram, Twitter (x.com), Reddit, and public RedNote posts.
_Avoid_: Platform, provider

**Canonical Source URL**:
The stable form of a Submitted Source URL used to de-duplicate composition inputs and identify repeated runs, with only recognized tracking parameters removed. The original URL remains in the Source Note.
_Avoid_: Clean URL, normalized URL
