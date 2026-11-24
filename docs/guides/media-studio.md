---
description: Cut, trim and export video and audio in Media Studio or from chat, and make pictures with your own image-service key.
---

# Media Studio and pictures

## Media Studio

Media Studio is VOOL's built-in editor for video and audio. Open the skills and plugins
window (**Skills & plugins…** in the command palette) and choose **Open Media Studio** on the
Media Studio card. It opens in its own window.

What you can do:

* **Trim** to a start and end, **delete a range**, or **split** at a point.
* **Crop**, **resize**, set the aspect ratio (16:9, 9:16, 1:1 or 4:5) and **rotate** by 90,
  180 or 270 degrees.
* Change the **volume** (0 mutes) or **normalize** the audio.
* **Undo** and **redo** each change.
* **Export** the result as a new file. Your original file is not changed.

You can also edit from chat: ask, for example, "trim the first 10 seconds off holiday.mp4 and
make it square". Chat and the editor use the same edits, so an edit made in chat shows up in
the editor and can be undone there. Saving the edited project as a new file asks for your
approval.

Good to know:

* Media Studio needs FFmpeg installed on this computer. Without it, edits report that the
  tool was not found.
* Heavy work runs in a separate helper process, so a broken or very large file cannot take
  VOOL down; it comes back as an error instead.

## Make a picture with your own key

VOOL can make pictures with your own fal.ai key. Nothing is set up on a fresh install.

1. In a chat, type `image key` followed by your fal.ai key. VOOL seals the key in its
   encrypted store on this computer, masks it out of the chat, and turns picture making on.
   It uses the FLUX schnell model unless you add another fal model name after the key.
2. Ask "generate an image of a red bicycle in the rain". VOOL sends your description to
   fal.ai, asking first when your mode says so.

`image key` alone tells you whether a key is set. `image key forget` removes the key and
turns picture making off. The key is sent only to fal.ai, and only when you ask for a
picture. fal.ai bills your own account. VOOL makes up to 3 pictures a day.

## Not in this version

* Making pictures on this computer without a cloud key.
* Making videos.
