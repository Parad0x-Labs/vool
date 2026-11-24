---
description: Use VOOL for everyday tasks — read, search, draft and send email with your approval, see your calendar, set reminders, notes and a to-do list, get answers as files, check live prices and currency, and find files.
---

# Everyday assistant

Ask in your own words in any chat. This page covers email, calendars, reminders, notes and the
to-do list, answers as files, live prices and finding files. Anything that sends, changes or spends asks first, as your
mode says (see [Permissions](../concepts/permissions.md)).

## Email

### Add an email account

1. Open **Settings → Email → Email accounts** and choose **Add email account**.
2. Under **Which email do you use?**, pick your service, or **Other** to type the server
   settings yourself (**Incoming mail server (IMAP)**, and optionally **Outgoing mail server
   (SMTP)**).
3. Enter the **Email address** and **Password**. Gmail, iCloud Mail and Yahoo Mail need an
   app password, not your usual one; the dialog shows the steps and **Open the app password
   page**.
4. Press **Check and save**. VOOL signs in to your mail server once to check the details and
   saves them on this computer only if the server accepts them. *Saved. VOOL can now use*
   your address.

Choose **Use as default** for the mailbox VOOL should use when a chat does not name one. VOOL
never switches to another mailbox on its own. Removing an account deletes its saved password
from this computer and changes nothing on the mail server.

Good to know:

* Outlook, Hotmail and Live addresses can't be added yet: Microsoft only lets other apps in
  through its own sign-in, which VOOL does not have yet.
* Without an outgoing server, VOOL can read the mailbox but not send from it.
* If this computer is set to keep everything on this machine, VOOL does not contact the mail
  server and nothing is saved.

### Read, search, draft and send

Once an account shows as ready, ask for example "what's new in my inbox?", "find the email
from the landlord about the deposit" or "draft a reply saying I'll be there at 6".

* Reading never marks messages as read.
* VOOL writes a draft first. You approve the exact text before it is sent; if the draft
  changes, it needs approval again. An approved draft is sent once.
* VOOL sends at most 20 emails a day.
* To end every email VOOL writes with your signature, type it in **Settings → Memory &
  Personalisation → Email signature** (up to 400 characters) and press **Save**. Empty the box
  and save to remove it.
* If VOOL cannot tell whether an email went out (for example after a crash), it holds new
  sends until you check under **Settings → Email → Sending hold**, which looks for the
  message in your Sent folder.

## Calendars

Open **Settings → Calendars → Calendar accounts** and choose **Add a calendar account**.
Sources are Google Calendar, Microsoft 365, an iCloud or other CalDAV server, and **Apple
Calendar on this Mac** (in VOOL on a Mac only; Google and Outlook calendars already in the
Mac's Calendar app show up there). No password is typed in this card: VOOL uses a sign-in you
have already saved.

After adding an account, tick the calendars VOOL should read and turn on **Sync this
account**. Nothing is read before that. **Use for new events** picks where new events go, and
**Alerts from this account** sets reminders a number of minutes before each event. Online
calendars are read only while **Go online on its own** is on; calendars on this computer keep
working without it.

In chat, ask for example "what's on tomorrow?", "when am I free on Friday for an hour?" or
"put lunch with Sam on Thursday at 1". Reading the agenda is local. Adding or changing an
event asks first. Calendar alerts arrive in the bell; see
[Notifications](notifications.md).

## Reminders and notes

* "Remind me at 5 to call the bank" sets a reminder. At the due time VOOL tells you in that
  chat and in the bell. You can also ask to list, move or cancel reminders. A reminder is not
  a calendar event, and a time in the past is refused.
* "Save a note: …" saves a note as a file in the chat's project folder. Ask "find my notes
  about the boiler" or "show the note …" to read them back. VOOL keeps exactly what you
  asked it to save, and never saves from a private chat.
* VOOL keeps a to-do list for you. Say "add milk to my to-do list", "tick off milk", "take
  milk off my to-do list" or "what's on my to-do list". This is the same list the Today box
  shows. If you name a file instead, such as "what's in my todo.txt on the Desktop", VOOL reads
  that file. In a private chat nothing new is saved to the list, and if an item matches more
  than one entry VOOL asks which one you mean.
* On a Mac, VOOL can list and read your Apple Notes when you ask, for example "read my Apple
  note called Packing list". It never changes them.

## Get an answer as a file

Ask for the answer as a file, for example "give me that as a Word document", "export the
table to Excel", "make a PDF of this plan" or "zip these three scripts". VOOL replies with a
download link instead of text you have to copy.

It can make Word (.docx), Excel (.xlsx), PowerPoint (.pptx), PDF, CSV and TSV, JSON, YAML,
XML, HTML, Markdown and plain-text files, code files, and .zip or .tar.gz bundles of several
files. VOOL opens each file again before it offers it, and a file that does not open
properly is not offered.

Good to know:

* One file can be up to 20 MB. A bundle holds up to 50 files, a slide deck up to 200 slides,
  and a sheet up to 100,000 rows.
* A file is kept for about a week, so download it before then.

## Live prices and currency

Ask "what's the euro to dollar rate?", "how much is 250 GBP in yen?" or "price of bitcoin".
VOOL looks the price up when you ask. A price it recorded earlier is never shown as the
current one.

This needs **Web lookups** on in **Settings → Privacy & Permissions** (off on a fresh install),
and a chat that is not set to **Offline**. A CoinGecko key in **Settings → API Keys** raises
the limits for crypto prices; prices work without one.

## Find files

Ask "find the photo called day 4", "where is my tax letter" or "which video in Movies is the
biggest". VOOL looks in your project folder and your visible home folders only when you ask,
matches everyday spellings such as `Day_4.png`, and does not index your drive in the
background. "How big is the holiday photos zip" answers with the size of that file or folder.
What it may read or change is in [Workspaces](../concepts/workspaces.md).
