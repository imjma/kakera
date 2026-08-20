# Telegram Intake is Transient-only

A private message to the bot that contains a Source Service URL becomes a Transient Telegram Delivery, not a Capture. Intake is a phone-to-group share: storing a Source Note and Attachments was rejected, and Transient is the only video path (ADR 0003). There is no Share Receipt, so a later message with the same URL Delivers again; retry is a new message, not an Inbox checkbox.
