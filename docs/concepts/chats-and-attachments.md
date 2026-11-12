---
description: Chats, private chats, and the files and long pastes you bring into them: limits, what is kept, and what can be refused.
---

# Chats and attachments

A chat is one conversation with VOOL. Chats are kept on your computer. A chat can be linked to
a project folder, so its tools work in that folder (see [Workspaces](workspaces.md)), and you
can bring files into it as attachments.

## Attaching files

Use **Attach** next to the message box, or drag files onto it. Each file shows as a chip, and
it goes with the message you send next. The line under the box shows the limits:

* Up to 8 files per message, 10 MB per file and 25 MB per message.
* Text and code files, and PNG, JPEG, GIF and WebP images. PDF, Word (DOCX), archives such as
  ZIP, and video join them when this computer has what it needs to read them. The file picker
  only offers what this computer can read.

What the model gets is limited too: up to 120,000 characters of one file and 240,000 of all
files in a message. A longer file is cut at the end of a line, and the record says where. If
the model you use cannot read images, the chip says so, and the image is not sent to it.

Before an image is stored, its hidden details are removed: camera and location data (EXIF,
XMP, IPTC), and text kept inside PNG and WebP files.

A file you pick is used for that one message. When the answer is done, the file's contents are
deleted and a receipt of it is kept.

## Long pastes

Paste 4,000 characters or more and VOOL keeps it as a document of the chat instead of a
message, exactly as pasted. **Preview** shows its beginning. A document stays with the chat
until you erase it or the chat. Later questions in the same chat can use it: VOOL hands the
model up to 60,000 characters of the chat's earlier documents, newest first, and says where it
cut.

## When a file is refused

A refused file is never dropped silently. Its chip stays and says why, and the message
cannot be sent until you remove the file or retry it: *Remove or retry the failed attachment
before sending.* Common reasons:

* **Too large, or too many files.** Remove some, or send them in two messages.
* **Not a type VOOL reads, or not what its name says.** A file named `.txt` that is really an
  image or a program, or a PDF this computer has no reader for, is refused with its reason.
* **A credential inside.** A file or paste that looks like it carries a live key or token, such
  as a private key block or an access token, is refused and nothing is saved. Remove the secret,
  then attach or paste again.

These refusals have their own short codes, shown with the message. They are not in the
[Error Book](../ERROR_BOOK.md), which covers the app's other errors.

## Private chats

Say "private chat", "don't record this" or "off the record", and the chat stops saving from
that message on: not the messages, not a summary, not memory. A banner shows *Private chat:
nothing is saved*, and each private message carries a small ghost. Private messages do not
come back after a reload, and VOOL cannot recall them later. Say "turn recording back on", or
press **Turn recording back on**, to save the chat again from the next message. A private chat
stays private after VOOL restarts.

## Taking a chat with you

**Export** in the chat header saves the whole chat as text or Markdown, every message, with
attached files listed by name and never their contents. To move a chat to another computer,
or keep a signed copy, see [Backup and recovery](../help/backup-and-recovery.md).
