---
description: See where each answer was answered, what paid models cost, what is coming up today, and whether every part of VOOL is working.
---

# Check your VOOL

Four places show you what VOOL is doing, in plain words, read from what really happened:

* the line under each answer says **where it was answered**;
* **AI spending** shows what paid models cost and holds them to your limits;
* the **Today** box on a new chat shows what is coming up and what is still open;
* **VOOL Doctor** checks each part of VOOL and says what to fix.

## Where each answer was answered

The chip under each saved answer starts with where the answer was made:

| Word | Meaning |
| --- | --- |
| On this computer | Every model it used ran on this computer. |
| On your own network | Every model it used ran on a computer on your own network. |
| Online | Every model it used was a cloud service. |
| This computer + online | Some work stayed here and some went to a cloud service. |
| No AI model | No model was asked; for example, a tool answered directly. |
| Place not recorded | VOOL did not record where a request went. It may have left this computer. |

Press the chip to open **Where this ran**. It lists each model with its place, for example
"online (api.example.com)", how much text was sent, and whether that text included things VOOL
remembers about you, this chat so far, or results of tools VOOL used. A closing line sums it up:

* **Nothing you wrote left this computer.**
* **Nothing you wrote went online. It stayed on your own network.**
* **2.1 KB of text went online for this answer.**

This counts every request the answer made, not only the one that wrote the reply. A local answer
that also sent text to a cloud model, for example to sum up memory, shows as
**This computer + online**. A request stopped before it was sent is not counted.

For a shorter line that names the model, turn on **Show answer details** in **Settings → General → Answers**.
It is off to start with.

The rest of the chip is explained in [Receipts](../concepts/receipts.md).

## AI spending

Open **Settings → Usage & Budgets → Paid models**. **Spending on paid models** shows **Today** and
**This month**, each as an amount of its limit with a bar. Each amount says how it is known:

* **billed by the provider**: the provider's own bill;
* **estimated by VOOL from the words sent and received**: VOOL priced the words itself, because the
  provider did not report a bill;
* **held for answers whose cost is not known yet**: an answer still running, or one whose bill has
  not come in, counted at its highest possible cost until it settles.

If nothing was spent, it says **Nothing spent on paid models.** It also says when the day's count
starts again.

### Set your limits

1. Fill in **Most for one answer**, **Most in one day** and **Most in one month**, in US dollars.
2. Press **Save limits**.

Each limit must be between $0.01 and $10,000. One answer's limit cannot be more than the day's or
the month's, and the day's cannot be more than the month's. Leave a box empty to use VOOL's built-in
limit. There is always a limit.

### What the limits do

A paid answer that could go past a limit is not sent. VOOL checks before it contacts the provider.

* At 80% of a limit, the limit shows **Nearly used**, and a warning appears under the answer that
  got there, with **Change limits**.
* At 100%, it shows **Used up. Paid answers are not sent until it starts again or you raise it.**

Answers on this computer are free and never count. A chat app that answers with your cloud models
counts toward the same limits. For price guards per provider, see
[Spending limits and price guards](spending-limits.md).

## Today

A new chat opens with a **Today** box, read fresh every time, and again when you come back to the
window:

* **Reminders**: overdue ones first (**Was due ...**), then those due today and tomorrow.
* **Calendar**: today's events from the calendars you chose. If the calendar has not been read
  lately, the box says so instead of showing old events.
* **To do**: open items on your to-do list.
* **Not finished yet**: longer jobs that are **Working**, **Waiting to start**, **Paused**,
  **Waiting for your OK** or **Stopped part way**, with how many steps are done.

A reminder you set in a chat shows here on the next read. A part that could not be read says
**Could not be read right now.** rather than looking empty. Empty parts are left out, and with nothing
to show, there is no box. Each part shows up to eight items, then **and N more**.

## VOOL Doctor

Open **Settings → About & Diagnostics → VOOL Doctor** and press **Run the check**. It checks each
part on this computer. Nothing is sent online.

| Part | What it checks |
| --- | --- |
| Private model on this computer | The local model app is running and has a model downloaded. |
| Online model | Whether your cloud provider answered the last time VOOL tried. It does not try again. |
| Your folders | Whether this computer lets VOOL look in your Desktop, Documents and Downloads. |
| Phone | Whether a phone is paired and can reach VOOL. |
| Storage | VOOL can save to its data folder, its database passes a check, and there is enough disk space. |
| Updates | Whether VOOL is up to date, an update is waiting for a restart, or the last update did not finish. |

Each part says **Working**, **Needs attention**, **Not set up** or **Could not check**. **Not set up**
is a choice, not a fault: for example, no online model is fine if you only use this computer.
**Could not check** is never shown as working.

A part that needs attention says what is wrong in one sentence and how to fix it, usually with a
button that opens the right place: **Open Models**, **Open API Keys**, **Open Phone settings**,
**Open Updates** or **Open Backup**. On a Mac, if your folders are kept private from VOOL, it tells
you to open System Settings, then Privacy & Security, then Files and Folders, and allow VOOL.

The summary at the top counts the parts that need attention. Press **Check again** after a fix.

## Good to know

* None of these places sends anything online.
* Answers in a private chat have no chip, so they show no place.
* If something looks wrong in an answer, press **Report** under it; see
  [Bug reporting and privacy](../trust/bug-reporting.md).
