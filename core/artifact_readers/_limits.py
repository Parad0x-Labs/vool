"""Every bound a reader is allowed to spend, stated once, in one place.

A reader that decides its own limits is a reader whose limits nobody can audit. These are the
numbers; each says what it protects. They are deliberately *hostile-input* numbers, not
happy-path numbers: the sizes below are what a decompression bomb, a 40,000-member archive or a
90-minute video meet, and they are enforced during decoding, not after it.
"""

from __future__ import annotations

# --- documents ------------------------------------------------------------------------------------

#: Pages parsed out of one PDF. Beyond this the remaining pages are listed as omitted, so the
#: model is told the document continued rather than shown a silent stop.
MAX_PDF_PAGES = 400
#: Paragraphs plus table rows taken from one DOCX.
MAX_DOCX_BLOCKS = 20_000
#: Characters kept per extracted unit before that unit alone is cut (the cut is stated on it).
MAX_UNIT_CHARS = 200_000

# --- OOXML documents and workbooks (XLSX / PPTX) ---------------------------------------------------

#: Sheets read from one workbook. Beyond this the rest are listed as omitted, not silently gone.
MAX_SHEETS = 64
#: Non-empty cells read from one sheet, and the running total for one workbook. A million-cell
#: sheet is a listing attack wearing a spreadsheet.
MAX_SHEET_CELLS = 120_000
#: Rows with content rendered from one sheet, before the rest are declared omitted.
MAX_XLSX_ROWS = 20_000
#: Columns rendered per sheet grid. A single stray cell in XFD must not stretch every row to
#: 16,384 mostly-empty columns.
MAX_XLSX_GRID_COLUMNS = 128
#: Entries in a workbook's shared string table. Bounds the table parse, which runs before any
#: cell can be read.
MAX_SHARED_STRINGS = 120_000
#: Slides read from one deck. Beyond this the rest are listed as omitted.
MAX_PPTX_SLIDES = 500

# --- RTF / ODT / EPUB ------------------------------------------------------------------------------

#: Brace nesting one RTF scanner will follow. Real documents sit under a dozen; hostile input
#: nests thousands deep to break recursive parsers.
MAX_RTF_GROUP_DEPTH = 512
#: Spine sections read from one EPUB. Beyond this the rest are listed as omitted, by position.
MAX_EPUB_SECTIONS = 500

# --- archives -------------------------------------------------------------------------------------

#: Members listed from one archive. A 40,000-entry archive is a listing attack.
MAX_ARCHIVE_MEMBERS = 2_000
#: Total uncompressed bytes this runtime will produce from one archive.
MAX_ARCHIVE_TOTAL_BYTES = 256 * 1024 * 1024
#: Uncompressed bytes from one member.
MAX_ARCHIVE_MEMBER_BYTES = 32 * 1024 * 1024
#: Uncompressed-to-compressed ratio of one member. 42.zip's inner members exceed this by orders
#: of magnitude and are refused on the ratio before the bytes are produced.
MAX_ARCHIVE_EXPANSION_RATIO = 200
#: Members whose CONTENT is read in one pass. Listing is cheap; reading is not.
MAX_ARCHIVE_READ_MEMBERS = 24
#: Archive nesting followed. Depth 1 = an archive inside the attached archive is LISTED, never
#: auto-extracted; depth 0 would not even name it, which hides the shape of the container.
MAX_ARCHIVE_DEPTH = 1

# --- images ---------------------------------------------------------------------------------------

#: Decoded pixels for one image. Guards the decompression bomb whose header claims 60000x60000.
MAX_IMAGE_PIXELS = 50_000_000
#: Longest edge an image is scaled to before OCR. Vision does not need the full raster.
OCR_MAX_EDGE = 3_000

# --- video ----------------------------------------------------------------------------------------

#: Seconds of video this runtime will consider. Longer input is sampled across its whole length
#: and the truncation of ATTENTION (not of the file) is disclosed.
MAX_VIDEO_DURATION_S = 3 * 3600
#: Frames decoded for one video, total, across every pass.
MAX_VIDEO_FRAMES = 24
#: Frames of the initial evenly-spaced sweep. The remainder of the budget stays available for a
#: question-directed second pass, so "what happens at 4:12" is answerable without a re-upload.
VIDEO_SWEEP_FRAMES = 8
#: Longest edge of an extracted frame.
VIDEO_FRAME_MAX_EDGE = 1_024

# --- audio ----------------------------------------------------------------------------------------

#: Seconds of one attached audio file this runtime will transcribe. Beyond this the transcript
#: covers the OPENING minutes and says so -- truncation of effort, stated, never silent.
MAX_AUDIO_TRANSCRIBE_S = 600
#: Bytes one attached audio file may occupy for its transcription to be attempted. Speech
#: recognition is CPU-bound; an hour-long FLAC is a denial of service wearing a podcast.
MAX_AUDIO_BYTES = 64 * 1024 * 1024
#: Seconds of one DICTATION recording this runtime will transcribe. A composer note, not an
#: archive; the composer sends its own shorter request and this is its ceiling.
MAX_DICTATION_S = 120
#: Bytes one dictation recording may occupy. Roughly an hour of AAC at 48 kbps -- far past the
#: duration bound, kept so a runaway recorder cannot stream forever either.
MAX_DICTATION_BYTES = 16 * 1024 * 1024
#: Recognised characters kept from one transcription before the text is cut (the cut is stated).
MAX_AUDIO_TEXT_CHARS = 100_000

# --- process confinement --------------------------------------------------------------------------

#: Wall-clock for one external decoder invocation.
SUBPROCESS_TIMEOUT_S = 90
#: Wall-clock for the whole extraction of one attachment, external calls included.
EXTRACTION_TIMEOUT_S = 180
#: Bytes of stdout accepted from an external decoder, enforced WHILE reading: the pipe is drained
#: in chunks and the process group is killed the moment the cap is passed, so a decoder that
#: streams gigabytes never gets to buffer them in this process.
SUBPROCESS_MAX_OUTPUT_BYTES = 64 * 1024 * 1024
#: Bytes of stderr accepted. Smaller: stderr carries diagnostics, and a decoder looping on a parse
#: error can produce them faster than stdout.
SUBPROCESS_MAX_STDERR_BYTES = 2 * 1024 * 1024
#: CPU seconds one decoder may burn (RLIMIT_CPU via the shell, which macOS does honour).
#:
#: There is deliberately NO address-space ceiling: `ulimit -v` / RLIMIT_AS is not settable on
#: macOS (measured 2026-09-03 -- the shell reports "cannot modify limit"), so claiming a memory
#: bound here would be a claim nothing enforces. Memory-hungry input is bounded instead by the
#: CPU limit, the wall-clock deadline and the aggregate scratch quota below.
SUBPROCESS_CPU_SECONDS = 60
#: Open files one decoder may hold.
#:
#: There is deliberately no child-process ceiling: RLIMIT_NPROC is per-UID, not per-process, so a
#: value low enough to bound a fork bomb also breaks every ordinary decoder that spawns a helper
#: (measured 2026-09-03 -- at 64 they exited 128 before doing any work). Runaway children are
#: bounded by killing the whole process group instead.
SUBPROCESS_MAX_OPEN_FILES = 256
#: Temporary bytes one extraction may write. Enforced by a watchdog WHILE the decoder runs, not
#: only when it returns -- an over-budget decoder that is allowed to finish has already spent the
#: disk the limit exists to protect.
MAX_SCRATCH_BYTES = 512 * 1024 * 1024
#: How often that watchdog measures. The overshoot a decoder can achieve between two ticks is
#: bounded by this interval times its write rate, and is stated rather than pretended away.
SCRATCH_POLL_SECONDS = 0.25
#: Directory entries one measurement will visit. A decoder that creates more files than this turns
#: the watchdog itself into the denial of service it exists to prevent, so the scan stops and
#: reports the scratch as over budget -- unmeasurable is over, which fails closed.
MAX_SCRATCH_SCAN_ENTRIES = 20_000
