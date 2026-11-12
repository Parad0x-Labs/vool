---
description: X integration — read X with your own developer keys, and post, reply, like or follow only after you approve each one.
---

# X integration

VOOL can read X (formerly Twitter) for you with your own X developer keys: look up an
account, search posts, read timelines and lists. With your account keys it can also act for
you (post, reply, like, repost, follow, send a message), but **only after you approve each
action in Settings**. Nothing is ever sent to X on its own.

X is **off on a fresh install**. Nothing goes to X until you connect your keys and choose
which X features VOOL may use.

## Connect X

Open **Settings → API Keys → X** and choose **Connect X**. There are two ways in:

* **Research public posts (Bearer Token).** Paste your app's Bearer Token from
  developer.x.com → your app → Keys and tokens. VOOL asks X for one public profile to check
  the token, then saves it.
* **I have my keys (my X account).** Paste the four keys your X app shows: Consumer Key and
  Secret, Access Token and Secret. VOOL makes one signed read to ask X which account the
  keys belong to and shows you that account. It saves the keys only when you choose
  **Save this X account**.

If you want VOOL to post for you, set your X app's user authentication settings to
**Read and write** (and **Direct message** for messages) before you generate the access
tokens.

**Cancel** saves nothing. If X refuses a key, is busy or does not answer, nothing is saved
either. Your keys are kept encrypted and are never shown again after you save them.

## Choose your X features and limits

Under **X features and spending** you pick the features VOOL may use, which match what your
X access allows. Features you leave unselected cannot run. You also set:

* how much VOOL may reserve per call for a feature that costs money (enter zero only when
  your X access makes the feature free);
* a maximum per call, per chat, per day and per month.

Turning features on or changing them asks for this device's approval. **Turn calls off**
stops every X call at once.

X bills your own X developer account. Under **X usage this month** VOOL counts the posts
and accounts it read and the things it sent for you, and stops at the limits you set; a box
left empty means no limit. A read is refused before it is sent once your limit is reached.
**Start counting from zero** resets VOOL's count only: X keeps its own count.

## Ask in chat

Ask in your own words, for example:

* "Look up the X account @name"
* "What did @name post this week?"
* "Search X for posts about the harbor festival"
* `Post this on X: "The first tomatoes are red."`

A read goes to X once and the answer uses what X returned. In **Manual** mode VOOL asks
before each X request goes out, and the card names the X operation, for example
"X: Get Users by Username (username name)".

Anything that acts on X is only **proposed** in chat. It waits for you in Settings, and
nothing is sent until you approve it.

## Approve or decline

Proposed actions wait in **Settings → API Keys → X** under **Waiting for your approval**.
Each card shows exactly what will be sent: the words of a post, and the post or account a
reply, like or follow touches. What you approve is exactly what is sent; it cannot change
after it is shown.

* **Approve and send** sends it once.
* **Decline** sends nothing.

**Recent** lists what happened to each action: Sent, Declined, Expired, Not sent, or
"Unclear if it was sent" when the answer from X was lost. VOOL never retries a declined,
expired or failed action on its own.

## X bookmarks

Reading and changing your bookmarks needs a separate sign-in with X. In your X developer
app, turn on OAuth 2.0 for a public client and register the callback address VOOL shows,
then paste the app's Client ID under **X bookmarks**. Every bookmark request, even reading
a page, waits for your approval.

## Privacy

* Requests go only to X's own API address, through the same outbound door as the rest of
  VOOL. Local Only mode stops them, and your keys never follow a redirect.
* If you send web traffic through **Private web** (Tor or a VPN), X requests use that
  route too. When the route is down, nothing is sent to X.
* The X settings answer only this computer. Another device on your network cannot read or
  change them.
* A request that waits for approval, and its answer, are kept encrypted on this computer.
