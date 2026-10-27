# Lend a job to a friend

Planned for v0.7.6, not released yet.

**Not reviewed yet. Use test files only.** Lending uses Drop, whose session
protocol has not had an independent security review. Both switches start off.

Your friend needs VOOL too. Their file is readable on the computer doing the
work, so they must trust the person lending it. The shared folder carries sealed
messages; it is not a place to put the original file yourself.

## Jobs available in this version

- Make a contact sheet from one photo or a ZIP of JPEG, PNG or WebP photos.
  There can be at most 100 photos. The size limit applies to the uploaded file
  and to the total unpacked photo files. Archive filenames are never extracted.
- Make a clean PDF from UTF-8 text, Markdown, PDF or DOCX. This keeps the text in
  a fresh layout, not the original pictures, formatting or interactive content.
  Empty text, unreadable files and content over the agreed limit are refused.
- Trim or convert an audio/video recording to a mono 16 kHz WAV file, when the
  local media tools are installed. The lending person chooses the start and
  excerpt length (up to five minutes). Input recordings are limited to one hour.
  Original metadata is removed from the WAV result.

Execution currently needs macOS process confinement. A computer without it
does not offer these jobs. A guest can use a supported Drop installation without
running a worker locally. Local-model summarising and translation are unavailable:
this version does not include an independently isolated model executor. There is
no cloud fallback or paid processing.

## Set it up

1. On both computers, open **Settings → Backup & restore → Send with Drop**.
   Turn on Drop and choose the same private shared folder.
2. Open **Lend jobs to friends**, turn it on and save. Compare fingerprints in
   person or on a trusted call. The invitation alone does not authenticate a friend.
3. Choose **Lend a job**, the job and its limits. Approval is required by default.
   Only select **Run without asking within these limits** if you want that
   invitation to run automatically.
4. Create an invitation. Its QR and copyable link contain the same job terms.
   Your friend chooses **Borrow a job** in their VOOL, pastes the link, reviews
   the terms and confirms your fingerprint before joining.
5. The lending computer confirms those same terms over Drop. Only then can the
   guest choose and send a file. Incoming requests appear in the lending
   computer's notification inbox and in Settings. Open the request, then choose
   **Run** or **Decline**. Settings does not need to remain open for delivery.
6. The result returns to the guest, who can save it. The receipt on both computers
   identifies the terms, input and result, the finish time and time used.
   An interrupted run can have an unknown duration; it is never reported as zero.

The default is one job, ending tonight at 23:59 on the lending computer. An
invitation can permit at most ten jobs and last at most seven days. Input and
output files are limited to 10 MiB each, and a job can run for at most 120 seconds.
The guest cannot change the capability, limits or automatic-run choice.
At most 100 host invitations can remain open at once. Revoke one or let it expire
before opening more; retained history does not use that allowance.

Each job runs in a fresh disposable worker home, offline, with its guest file
and the fixed job program. It does not receive the lending person's chats,
memory, contacts, accounts, credentials or other files. Content inside a document
cannot select another job or grant tools. The worker home is removed after the
attempt, including failure and timeout. Interrupted workers are supervised by
recorded process identities and recovered after a restart. This is file removal,
not a claim of secure physical erasure on an SSD.

## Waiting, refusal and expiry

A job allowance is consumed when it is submitted/accepted, including a decline
or failed attempt. Replaying a request does not restore it. Create a new
invitation if you want to allow another attempt.

If the lending computer has not answered within 30 seconds, the guest sees
that it is asleep or unavailable and can check later. A received request can
wait for the person's approval until expiry. Expired, revoked, oversized,
failed and timed-out jobs have explicit states. A partial result cannot be saved
as a complete one.

Revoking an invitation refuses new work and declines queued work. Revocation
remains available while a worker runs; any resulting output is withheld. Turning
lending off revokes its open host invitations. Expiry removes staged and received
files when the daemon checks them. Keep the original file yourself.

The current transfer transport is a shared folder. Direct guest pages for people
without VOOL, routine lending and paid jobs are not part of this version.
