---
description: Make, try, change, share and remove your own VOOL skill, all by asking in chat.
---

# Make your own skill

## What a skill is

A skill is a short set of instructions that VOOL remembers for a job you do again and
again. You write it once, in your own words, and from then on VOOL follows it whenever you
ask for that job. For example: "whenever I give you a shopping list, sort it by supermarket
aisle". A skill only tells VOOL *how* to do something. It never gives VOOL new permissions:
VOOL still asks before it does anything that needs your OK.

You do everything below by typing in an ordinary VOOL chat. There is no form to fill in.

## 1. Ask for it

Say what the skill should do, the way you would explain it to a friend. Start with
**make me a skill** and say when it should be used:

> Make me a skill for all of VOOL: whenever I give you a shopping list or a grocery list,
> sort it by supermarket aisle.

You can say where the skill should work:

| Say | The skill works in |
| --- | --- |
| **for this chat** | only this chat |
| **for this project** | every chat in this project (the chat must belong to a project) |
| **for all of VOOL** | every chat. This is also what you get if you say nothing. |

Put **that** after the place: *make me a skill for this project that ...*. You can also give
it a name straight away: *make me a skill called aisle-sorter that ...*.

## 2. Give it a name, if VOOL asks

When an AI model is connected, VOOL writes the skill for you, picks a name and shows you the
draft. When no model answers, VOOL writes the skill from your own words and asks:

> What should I call this skill? For example: Shopping list by aisle.

Type a short name, for example **Shopping list by aisle**. Words like "ok" or "thanks" are not
taken as a name; VOOL asks again. To stop, just ask about something else.

VOOL then shows you the draft:

* **When it applies:** the situation the skill is for.
* **Trigger words:** short phrases that tell VOOL to use the skill (more on these below).
* **What it tells me to do:** the instructions themselves.

Read it. A draft written without an AI model copies your words, so its trigger words can look
odd; the skill is still picked by its name and the words you used. If something is wrong, change
it the way step 7 shows, before you turn it on.

## 3. Allow

In **Manual** mode (the usual setting) a **Permission needed** bar appears when VOOL wants to
save the draft. Click **Allow once**. Nothing is switched on yet: a draft is only kept, not
used.

If you click **Deny**, nothing is saved. You can ask again later.

## 4. Turn it on

Say:

> turn it on

VOOL asks once more with the **Permission needed** bar (it asks for this step in Auto mode
too). Click **Allow once**. VOOL replies:

> Added the skill "Shopping list by aisle". It is on from your next message.

The skill works where you asked for it in step 1 (this chat, this project or all of VOOL).

## 5. Try it

Send a new message, the way you would normally ask. Try two or three different wordings, so
you know it works however you say it:

* *Here is my shopping list: milk, apples, bread, frozen peas*
* *Can you sort my grocery list? eggs, rice, soap*
* *Put these in supermarket aisle order: tea, yoghurt, bananas*

Then try one message that has nothing to do with it, such as *write me a short poem about
autumn*. The skill should stay out of that one.

Tip: ask your skill to start or end its answer the same way every time (for example, "start
with the heading Shopping list by aisle"). Then you can see at a glance when it was used.

## 6. See your skills

Ask:

> which skills do I have?

VOOL lists them in groups: **Made by you**, **Built in**, **Added from Browse** and **Drafts
not turned on yet**. Each line says whether a skill is **on**, **off** or **not available
here**, and what it is for. A skill made for one chat or project says so, for example
"Shopping list by aisle, for this chat".

## 7. Change it

Name the skill and say what to change. Put the name in quotes so VOOL knows exactly which
skill you mean:

> Change the skill "Shopping list by aisle" so that it sorts by aisle and puts frozen food last.

Describe the whole job you want, not only the new part, with the words you will really use.
When no AI model answers, VOOL writes the new version, trigger words included, from the words
of this message alone.

VOOL shows the new version and says it is not on yet. Say **turn it on** and click **Allow
once**. This time VOOL says **Updated** instead of Added:

> Updated the skill "Shopping list by aisle". It is on from your next message.

Changed your mind? Every version is kept. Say *roll back the shopping-list-by-aisle skill*
and VOOL lists the versions it has; then say which one, for example *roll back the
shopping-list-by-aisle skill to version 1*, and click **Allow once**. The old version is used
from your next message.

## 8. Share it

> Share the skill "Shopping list by aisle"

(*export the skill "..."* works too.) Click **Allow once**; VOOL saves the skill as a zip file and tells you where.
Send that file to a friend. They attach the zip to a message in their VOOL chat and say:

> import this skill

or *import this skill for this chat*. VOOL shows them the skill first. It is not on until they
say **turn it on**. Share a skill while it is on: a skill you turned off cannot be shared.

The zip holds only the skill's instructions. VOOL refuses a zip that carries programs or other
files.

## 9. Turn it off or remove it

> Turn off the skill "Shopping list by aisle"

*Remove the skill "..."* and *delete the skill "..."* do the same thing. After you click
**Allow once**, VOOL replies:

> Turned off `shopping-list-by-aisle` for all of VOOL. Its files and versions were kept.

VOOL stops using it from your next message and it leaves *which skills do I have?*; a copy is
kept. There is no "turn it back on" by name: to use it again, import its zip file (step 8) or
make it again. This works for skills you made or imported, not
for the skills that come with VOOL.

## How VOOL decides to use your skill

Before each answer, VOOL compares your message with the **name**, the **When it applies**
sentence and the **Trigger words** of your skills. A skill is used when one of these is true:

* your message names the skill ("use the aisle-sorter skill");
* your message contains every word of one of its trigger phrases (with the trigger *grocery
  list*: "sort my **grocery list**");
* your message shares at least two meaningful words with the skill ("sort ... aisle").

One ordinary shared word is not enough. *What is the best supermarket near me?* shares only
"supermarket", so the skill stays out of it. Everyday words such as *write*, *make*, *help*,
*list* or *explain* never count on their own. Small changes in word form still match:
*polite* and *politely*, *organise* and *organize*.

**Tip: give your skill clear trigger phrases.** When you ask for the skill, use the words you
will really say later, and name the different ways you say it: "whenever I give you a
**shopping list** or a **grocery list**". Those words become the trigger phrases you see in the
draft. If VOOL misses your skill later, change it and add the missing words.

## If something goes wrong

* **"What should I call this skill?"** No AI model wrote the draft. Type a short name.
* **"The draft ... was never saved -- its save was not approved"** after *turn it on*. The draft
  was not allowed. Ask for the skill again and click **Allow once**.
* **VOOL does not use your skill.** Check that it says **on** in *which skills do I have?*, and
  that you are in the chat or project it was made for. Add the words you really use as trigger
  phrases (step 7), or name the skill in your message.
* **VOOL uses your skill when you did not want it.** Make its trigger phrases more specific, or
  turn it off.
* **"I need to know what it should do"**. VOOL could not tell what you asked for or which skill
  you meant. Start with *make me a skill that ...*, or put the skill's name in quotes, exactly
  as the skill list shows it.
* **"I can't keep a skill for this project"**. This chat is not part of a project. Say *for
  this chat* or *for all of VOOL* instead.
* **"I did not import ..."** VOOL explains why. A skill zip may only hold the skill itself, plus
  an optional read-me and licence.
* **"The draft changed after it was shown"**. The draft was replaced after you saw it, so VOOL
  did not turn it on. Ask for it again.

For the file format, testing from a computer and publishing, see
[Build and test a skill](build-and-test-a-skill.md).
