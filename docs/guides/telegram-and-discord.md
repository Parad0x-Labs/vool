---
description: Chat with your VOOL from Telegram or Discord - make a bot in a few steps, choose what it may do, approve actions on your computer, and read what happened under each answer.
---

# Chat with VOOL from Telegram and Discord

You can talk to the VOOL on your computer from Telegram or Discord, on any phone or computer where
you use them. You make a small bot of your own, connect it to VOOL, and chat with it in a private
conversation. VOOL still runs on your computer: the bot only carries your messages.

Telegram and Discord are the two chat apps VOOL connects to.

Telegram and Discord can read messages sent through them. Use a private conversation, and keep
private things in VOOL itself.

## What you need

* VOOL open on your computer, with an internet connection. Your computer must be on for the bot
  to answer.
* A Telegram or Discord account. For Discord, a server you manage (you can make one in Discord by
  pressing **+**).

You do not need to switch on phone access for this.

## Connect Telegram

1. On your computer, open **Settings → Phone and chat apps** and press **Connect Telegram**.
2. **Step 1 of 3: make your bot.** Press **Open BotFather**. In Telegram, send `/newbot` to
   BotFather, then choose a name and a username. BotFather answers with a key. Copy it.
3. **Step 2 of 3: bring the key here.** Come back to VOOL. It picks the key up from the clipboard
   when it can. Otherwise press **Paste from clipboard** or paste it into the box.
4. **Step 3 of 3: say hello.** Press **Open in Telegram**, or scan the code with your phone, and
   press **Start** in Telegram. Do this within five minutes.
5. Your computer asks whether the person who pressed Start is you. Press **Yes, that's me**.

VOOL says **Connected to** *your name*. **Only this account can use this bot.**

## Connect Discord

1. Open **Settings → Phone and chat apps** and press **Connect Discord**.
2. Press **Open Discord developer page**. Press **New Application** and give it a name. Open
   **Bot**, press **Reset** and copy the key.
3. Paste the key in VOOL.
4. Press **Add to Discord** and add the bot to a server you manage. VOOL then opens your private
   chat with it.
5. If Discord wants you to say hi first, press **Say hi to VOOL**, press **Message** and send
   "hi". The settings page changes to **Connected** by itself.

If a step does not work, **Another way** offers a pairing code or a link to your private Discord
conversation instead.

## Chatting

Write to the bot in your private chat. No `@` is needed. VOOL does not read group chats or server
channels, so nobody else can use your bot.

The chat also shows in VOOL on your computer, so you can carry on there.

Under each answer, VOOL adds where it ran (for example **On this computer** or **Online**) and a
line for each action it took, with its state: **Done, checked**, **Done**, **Sent**, **Waiting for
your OK**, **Not done**, or **Reported done, but VOOL could not find it**.

## Choose what the bot may do

After connecting, the bot may only chat. Everything else is off until you turn it on under **What
this bot may do**, then press **Save permissions**.

| Choice | What it allows |
| --- | --- |
| Read chat history | Read earlier messages of this chat, with `/history` |
| Send attachments | Send photos and files to VOOL from the chat app |
| Alerts | Reminders, finished tasks and other alerts in the chat. Choose **Everything**, **Important only**, **Off** or **Custom**, and set quiet hours |
| Files in chosen folders | VOOL can find files in the folders you list and send them to you in the chat. Nothing else on the computer is reachable |
| Reminders, calendar, notes and contacts | VOOL can read and change these from the chat app. Deleting contacts, apps, commands, email and money stay on your computer |
| Answer with my cloud models | Answers may use your cloud models. Paid answers count toward your AI spending limits and stop when a limit is reached. With this off, answers come from the models on your computer |
| Wallet balances and receipts | `/balance`, `/receipts` and `/contacts` |
| Propose payments | Start a payment with `/pay`. It is approved on your computer |

Files, everyday tools and cloud models stay on for 30 days at a time. Turn them on again when
they run out.

A bot can never use apps, commands or your accounts on the computer. Ask in VOOL on the computer
for those.

## Approvals happen on your computer

When something needs your OK, the bot says **This needs your OK on your computer**, and what it
is. Open that chat in VOOL on your computer and allow or deny it.

* Allowed: VOOL finishes it and answers in the chat app.
* Denied: VOOL tells you in the chat app that it did not run.
* No answer in time: nothing is done. Ask again if you still want it.

A bot can never approve payments or actions, and it can never change its own permissions.

## Payments

Payments need a wallet set up on your computer, with crypto switched on (it is off by default).

1. Send `/pay` followed by the exact amount and a contact or address.
2. The bot shows what it would pay, with warnings such as a new payee or an unusual amount. Check
   it, then send the `/payconfirm` line it gives you.
3. The payment waits on your computer. Review its fees and approve it there.

Send `/freeze` at any time to freeze payments.

## Good to know

* A file VOOL sends you is kept by Telegram or Discord. Telegram takes files up to 50 MB, Discord
  up to 10 MB. A larger file stays on your computer.
* To stop, press **Disconnect chat app** in Settings.
* If connecting times out, paste the key again to get a new link.
