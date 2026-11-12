# My routines

Show VOOL a task once in one folder, and it can do it again for you. Nothing changes until you have looked at the exact list of changes and chosen **Run**.

Open **My routines** from the link beside Contacts, or with **Open My routines** in Settings.

## Teach a routine

1. Choose where the folder is: Desktop, Downloads or Documents, or a folder inside one of them. Hidden folders, links to somewhere else and VOOL's own data cannot be used.
2. Choose **Watch this folder while I do the task**, then do the task once, the way you always do it: rename the files, move them into a folder, and add a row to your list (a `.csv` file in that folder) if you keep one. VOOL only looks at that folder, for at most 30 minutes, and notes how its files change. It does not look at your screen, your keys, other folders or other apps.
3. Choose **I'm done**. VOOL lists what it saw. Choose **Make a routine from this** to read the routine it proposes: which kinds of files, their new name, the folder they go into and the list row.

Each part of a name or folder is either words you typed or a value VOOL can read again next time: the file's date, year, month, day or original name, or a receipt's date, shop and amount. When your examples do not agree, VOOL says so and starts from the first one for you to correct. It does not guess.

Give the routine a name, choose **Try it** to see what it would do with the files in the folder now (nothing changes), then choose **Save the routine**.

What the watch saw is deleted when you save the routine or choose **Forget what VOOL saw**. If VOOL was closed when the 30 minutes ran out, nothing from that watch is kept.

## Look, then run

**See what it would do** shows the exact list: each file, its new name and folder, any folder that will be made, any row that will be added, and the files left for you with the reason.

**Run these changes** runs that list and nothing else:

- If anything in the folder changed since you looked, nothing runs and VOOL shows you the new list.
- A run never replaces or deletes a file. Each file is checked once more right before it moves; if it changed, the run stops there.
- The first problem stops the run. What was done stays done and is listed; nothing else is touched.
- After the run, VOOL checks that every moved file is in its new place with the same content.
- A run handles at most 25 files unless you choose another limit (up to 200). With more files than that, nothing runs.

**Past runs** keeps what the last 20 runs did. Nothing is replaced or deleted, so you can move any file back yourself.

## A routine on a schedule

You can choose **Every day** or **Every week** at a time. At that time VOOL works out the list, and when there is something to do or something needs you, it puts a note on the bell. A routine never runs by itself: the note opens the list in My routines, and you choose **Run**. A time missed while VOOL was closed is caught up once. A new or changed schedule waits for its next time.

## Receipts

A routine can use a receipt's date, shop and amount. VOOL reads the document on this computer, only inside process confinement (the macOS sandbox, or bubblewrap on Linux). On a computer that cannot read receipts, files that need those values are left for you. A value that cannot be read with confidence is left empty, and that file is left for you too.

## Change, share or remove

A routine is kept as one of your own skills. Every save keeps the version before; **Earlier versions** lets you go back to one.

**Share it** shows the exact contents of a small file before you save it: the routine's rules, without its folder, schedule, versions or runs. Email addresses, phone numbers, card and bank numbers, street addresses and the names of your saved contacts in its words are replaced with placeholders such as `[Email 1]`. This is a best effort, so read the file before you save it.

**Bring it in** reads a routine file someone shared with you. It does nothing until you choose its folder and save it.

**Remove it** removes the routine and its past runs. The files it moved stay where they are.

In the Mac app, a `vool://routine/...` link opens that routine's list in My routines.
