# X features and spending

Planned for 0.7.3, not released yet.

VOOL can call the documented X API v2 operations from a chat. Every call is off until you
turn it on, goes through one door that checks your choices and limits, and leaves a receipt.

## Off until you choose

- On a fresh install no X feature is enabled and nothing is sent to X.
- In Settings, under **X features and spending**, you name your X access profile, pick the
  features your X access allows, and set a cost reservation for each one plus four spending
  limits: per call, per chat, per day and per month.
- Saving a profile with calls enabled asks for a device approval. Turning calls off never asks.
- Prices are your own declarations of what X may charge you. VOOL does not measure or verify
  X's charges.
- A feature with a cost above zero also needs the wallet to authorize that reservation, and
  all four spending limits must be set above zero.

## One door for every call

Each call is checked in this order. If any check fails, nothing is sent.

1. The feature is in your saved profile and the profile has not changed since the request
   was prepared.
2. A paid feature's reservation is authorized by the wallet and held in the same spending
   record that model calls use, so one set of limits covers both.
3. The daily X call count and your monthly X read and action limits have room.
4. The profile and the connected X account are checked again just before sending.
5. VOOL sends the request once. It never retries a request on its own.

A receipt is kept for every call: the feature, the chat, the reservation, whether the request
went out and X's status code. Receipts identify the request with a keyed fingerprint, not
its text.

## What needs your approval

- Reads of public data run straight from the chat.
- Anything that changes your X account, and reads of your private bookmarks, wait in
  Settings. You see the exact request (credentials masked) and approve or decline it there.
- An approval is bound to the request you saw, your profile and the connected account. If
  any of them changes, the approval is refused and nothing is sent.

## When the outcome is unclear

- If X answers, a paid reservation stays held until billing is reconciled. VOOL does not
  treat an answer as proof of what X charged.
- If the answer is lost or cut off after the request went out, the call is marked unknown,
  the reservation stays held and the reads count against your limit. Check X before asking
  again.
- A reservation is released only when VOOL knows the request never left (for example the
  connection was refused, or a check failed before sending).

## Private copies

- Approved requests and their results are kept only in VOOL's encrypted local store. The
  list of X actions holds references, never the request or result text.
- The request preview is shown only in Settings. A result is shown to you and to the chat
  that asked for it.
- Copies are erased when the action is sent, declined, expires or is cleaned up. If the
  encrypted store cannot be opened, nothing is sent.
- Credentials that appear inside an X result are replaced with references. Only the chat
  that made the call can reuse them.

## Sign-in and bookmarks

- X calls can use the app Bearer Token, your four account keys, or an X sign-in. Each
  feature uses the first of these that X accepts for it.
- When you save or test X keys in Settings, VOOL checks them with one X read. That read
  counts against your monthly X read limit, even when its answer is lost, and the check does
  not start when the limit is used up.
- The sign-in is set up under **X bookmarks** in Settings: paste the Client ID of your X
  developer app (OAuth 2.0, public client, with the callback VOOL shows), then continue on X.
  It asks for read access to posts and users, bookmark read and write, and offline access.
  VOOL checks which account signed in, and that check counts as one read, even when its
  answer is lost.
- Bookmarks need this sign-in. Reading a page of bookmarks, adding one and removing one each
  wait for your approval in Settings. Disconnecting removes the saved sign-in.

## Limits

- A result can be at most 4 MiB. A streamed result stops after 100 records or 20 seconds.
- Before a stream starts, VOOL holds the most it could read (100 records) against your monthly
  X read limit, and then counts every record it received. If too little of the limit is left,
  the stream does not start.
- Reads in progress are held for every VOOL window and process at once, so two of them cannot
  both use the last part of your limit. If VOOL stops in the middle of a read, that read stays
  counted.
- The 20 second timeout applies to each wait for data. A server that keeps sending slowly can
  hold a call open longer.
- Four features need sign-in permissions that VOOL does not ask for (developer account,
  account setup, direct message media and Space buyers). Settings marks them as needing extra
  X permission and they cannot be turned on.
