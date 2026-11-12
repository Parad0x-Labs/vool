---
description: The bell in the header — what lands in each section, what Dismiss and Hide offer do, model offers, sound and system notifications.
---

# Notifications and model offers

The bell in the header is VOOL's inbox. Reminders, calendar alerts, approval requests, finished
chats and news about models land in one list with one unread count. The list is kept on this
computer, stays after a restart, and every VOOL window shows the same list.

## What lands where

Open the bell. Items sit in three sections, and the number beside a section is how many of its
items are unread.

| Section | What appears |
| --- | --- |
| Needs you | Reminders and calendar alerts, approval requests, chats that failed, and warnings about a model you watch: its price rose, it stopped being free, or the provider removed it |
| Updates | Chats that finished or were stopped, other changes in the model catalogue (a price drop, a model seen becoming free that did not qualify as an offer), alerts from a wallet or price you asked VOOL to watch, and phone pairing notices |
| Model offers | Offers that qualified for discovery: a model that became free under stated limits, or a recorded price cut by half or more from the same provider |

A model you watch is the model you picked in the model menu, and a paid model whose price you
accepted.

Some things never make an item:

* Greetings, quick commands and other turns that did no work.
* A finished, failed or stopped turn in a private chat, so the inbox never names that chat. An
  approval request from a private chat is still listed.

Every chat item comes from VOOL's own record of what ran, never from text a model wrote.

## Reading and dismissing

* Opening a section marks the items shown there as read. Other sections, and anything that
  arrives later, stay unread.
* A chat that finishes while it is on screen is marked read when its answer arrives.
* **Dismiss** hides an item. It does not approve anything, change your model, stop a job or turn
  off future reminders.
* A chat item opens its chat with **Open chat**.
* A reminder or calendar alert has **Snooze 10 min** on the item itself. Click the item for its
  card: **Snooze 5 min**, **15 min** or **1 hour**, and **Open chat** for the chat that set it.
  A calendar alert's card also offers **Join meeting** when the event has a meeting link, **Open
  in calendar**, and **Remind me** *N* **minutes before this event** for that event's alerts.
  VOOL does not change the alarms saved in your calendar.

The bell lists up to 200 current items.

## Model offers

Open the model menu in the header and choose **Discover models**, or press **Discover models**
at the bottom of the bell. It is always there, even when nothing has qualified: an empty list
means no model became free or cut its price by half with fresh evidence.

Each offer shows the observed prices, context length, tools and image support, the kind of offer
(standing price, temporary promotion, trial, free quota or provider subsidy), when it ends, when
the evidence was seen and its source. These are catalogue values VOOL observed, not a
certification.

* **Try once** runs your next message in this chat on that model for one turn. The chat then
  goes back to its own model.
* **Set as default** goes through the ordinary model choice, including the confirmation a paid
  model needs. If that check refuses, nothing changes.
* **Hide offer** removes the offer from discovery and its item from the bell. Dismissing only the
  bell item leaves the offer in discovery.
* **Preferences** turns discovery on or off, limits it to local or cloud models, to some
  providers, or to models with tool or image support, and sets how often it tells you (as it
  happens, at most daily or at most weekly per model).

Opening discovery never changes your model, and VOOL never switches models on its own.

### Where model news comes from

Model news comes from the OpenRouter model catalogue. VOOL checks it every hour only while **Go
online on its own** is on in **Settings → Privacy & Permissions**, and that switch is off on a
fresh install. With it off, the catalogue is read only when you ask, for example by typing
*refresh the model catalog* in a chat.

The first time VOOL reads the catalogue, what it finds is listed as already read instead of
arriving as a burst of alerts.

## Sound and system notifications

**Settings → Notifications** holds the rest.

* **Notification sound** plays a soft chime once when a chat finishes or needs your approval,
  and only while that item is new. Items that were already there when the window opened stay
  silent. **Preview sound** lets you hear it.
* **Quiet hours** stop the chime and system notifications between two times you choose. The bell
  still records every alert.
* **Show an alert that comes due late, up to** *N* **minutes after its event started** (0 to 60)
  decides whether a calendar alert that comes due late is still shown.
* On a Mac, **Also send VOOL alerts to macOS notifications** sends calendar alerts, reminders and
  chats that finished or need you to Notification Center as well. VOOL asks macOS for permission
  if it has not been given, and **Send a test notification** checks the path. You choose what a
  notification may show: the title and the details, the title only, or nothing personal. VOOL
  records when macOS accepts a notification; it cannot see whether a banner appeared. While this
  is on, macOS plays the sound and Focus settings apply.
* Model offers never send a system notification.
* On Linux, and when VOOL is open in a browser, there are no system notifications. Every alert is
  in the bell.

The **Watching** card on the same page lists the wallets and prices you asked VOOL to watch, with
**Pause**, **Resume** and **Remove**. To start one, ask in any chat, for example *watch this
wallet*.

## What permission or connection it needs

None for the bell itself: the inbox is stored on this computer and contacts nobody. Model news
needs the catalogue check described above. System notifications on a Mac need macOS's
permission.

## When something looks wrong

* **A finished chat is not in the bell.** It was a private chat, or the turn did no work.
* **Model offers stays empty.** Nothing qualified, or the catalogue has not been checked since
  **Go online on its own** is off.
* **An item looks updated but is still read.** A later observation of the same change adds
  detail to the existing item without changing whether you read or dismissed it. Separate price
  changes and expiring offers are separate items on purpose.
* **"Could not save read state."** The read mark was not saved. The items stay unread; open the
  section again.
* **No chime.** The sound is off, quiet hours are on, macOS owns the sound because macOS
  notifications are on, or the browser blocked playback. The bell says *Sound playback is
  unavailable. Notifications remain in the bell.* in that case.

## What to do next

* Answer waiting approval requests — see [Permissions](../concepts/permissions.md).
* Compare models before switching — see [Supported models](../reference/models.md).
* Set what a paid model may cost — see [Spending limits and price guards](spending-limits.md).
