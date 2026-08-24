# Kakera

A personal command-line tool that saves fragments of public source posts as local Captures and can publish Telegram Deliveries.

## Language

**Capture**:
A Source Note and referenced Attachments produced from one or more ordered submitted sources. A Capture may contain verified Source Posts and Failed Sources; Attachments can be referenced or reused by other Captures. A single-source Capture is the ordinary case; a composed Capture is the same model with additional sources.
_Avoid_: Download, saved post, bookmark

**Capture Tag**:
A user-controlled Obsidian tag on the whole Capture; service tags are generated from current sources, while Capture Tags come from capture requests or existing note tags and are normalized and deduplicated case-insensitively. `share/telegram` requests a Telegram Delivery only for the current Capture, then persists as metadata without retriggering later recaptures.
_Avoid_: Service tag, queue label

**Telegram Delivery**:
A copy of source images sent by Kakera to its configured Telegram chat, accompanied by a source-derived name and one source URL when available. A Transient Telegram Delivery may also include source video; a Delivery from a Capture or an Obsidian Note remains images only.
_Avoid_: Telegram capture, Telegram source, Telegram post, feed post

**Transient Telegram Delivery**:
A Telegram Delivery from a Submitted Source that is not retained as a Source Note or Attachments. It may include source video. It is not a Capture and has no Share Receipt. Inbox and Todoist request one with `share/telegram-only`. Telegram Intake requests one for each recognized Source Service URL.
_Avoid_: Capture, memory-only delivery, receipt-free capture

**Share Receipt**:
Evidence stored with an Obsidian Note that a sharing service acknowledged a Delivery to a particular destination. It distinguishes a first publication from an acknowledged repeat request; a Transient Telegram Delivery has none.
_Avoid_: Delivery history, sent tag, Telegram Receipt

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
A Submitted Source that could not be verified or supplied no supported image. Its URL and failure reason are written in the Source Note body; it is not called a Source Post. A composition is saved when another Source Post supplies at least one image. An Instagram login wall is an expired session; a followers-only Instagram post is followers-only.
_Avoid_: Ignored URL, skipped source

**Source Note**:
The Markdown file named for the Primary Source title, or its stable post ID/canonical hash fallback when no title is available, stored locally in `downloads/` or in the configured Obsidian notes folder. A colliding title becomes `Title - POST_ID - Service.md`. It contains Obsidian properties, available source text, and relative links to its Attachments.
_Avoid_: Metadata file, source file

**Obsidian Note**:
A Markdown note inside Kakera's configured Obsidian notes folder. A Source Note is one kind of Obsidian Note; other Obsidian Notes need not represent Captures.
_Avoid_: Capture, Source Note

**Attachment**:
An image referenced by a Source Note and stored under its Source Service in the local or configured Obsidian attachments folder. The same file may be referenced or reused across Captures.
_Avoid_: Asset, media file, downloaded image

**Queue Report**:
A private Telegram message to one configured allowed user about an Inbox or Todoist outcome of Submitted Sources: an unsuccessful group, a named Instagram Failed Source, a failed Transient Telegram Delivery, or a Capture or Delivery that could not be marked complete. It is not a message that Inbox or Todoist could not be read.
_Avoid_: Alert, notification, watch error, Telegram error, capture report

**Inbox**:
An Obsidian note containing unchecked Markdown tasks whose URLs are waiting to become Captures, or Transient Telegram Deliveries when requested with `share/telegram-only`. A parent task and nested subtasks may form one ordered composition. Successful groups are checked; unsuccessful groups remain pending. Named Instagram Failed Sources produce a Queue Report even when the group is saved; other unsuccessful groups produce one while they remain pending; a group that cannot be checked after success also produces one.
_Avoid_: Queue database, link file, Todoist

**Todoist**:
An external project of open tasks whose URLs are waiting to become Captures, or Transient Telegram Deliveries when requested with `share/telegram-only`, the same role as the Inbox. A parent task and nested subtasks may form one ordered composition; native labels become Capture Tags except `share/telegram-only`, which is a queue request and is not persisted. Unsuccessful groups remain open. Named Instagram Failed Sources produce a Queue Report even when the group is saved; other unsuccessful groups produce one while they remain open; a group that cannot be closed after success also produces one.
_Avoid_: Inbox, queue label

**Telegram Intake**:
Private Telegram messages to the bot from allowed users. A recognized Source Service URL in the message text or caption is a Submitted Source for a Transient Telegram Delivery to the configured chat. Only a new private message is a submission; an edit of a consumed message is not. Each consumed message is finished; it is not left pending and is not retried. Success is the Delivery in the configured chat; that Delivery names the sender by Telegram username, or display name when they have no username, not by user ID. A failed Delivery is reported in the sender's private chat with the failure reason.
_Avoid_: telegram-bot, Telegram Inbox, Telegram queue, Telegram post

**Source Service**:
The external service associated with a verified Source Post or recognized submitted source. Kakera v0.1 supports Instagram, Twitter (x.com), Reddit, and public RedNote posts.
_Avoid_: Platform, provider

**Canonical Source URL**:
The stable form of a Submitted Source URL used to de-duplicate composition inputs, identify repeated runs, and write the Source Note and Telegram caption. Recognized tracking parameters and Instagram post query/fragment are removed; the submitted tracking form is not retained.
_Avoid_: Clean URL, normalized URL
