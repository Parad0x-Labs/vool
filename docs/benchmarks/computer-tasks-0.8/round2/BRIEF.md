# Benchmark authoring brief (round 2)

You will invent ONE ordinary person's computer home folder and then write 45 test requests about it with exact answer keys. Both are used to test personal computer assistants. Nothing else is provided: invent everything yourself.

## Part A: the home folder
Invent a believable non-technical adult (any country, any job) and their home folder: 32 to 40 files spread over Desktop, Documents (with 2-4 subfolders), Downloads, Pictures (with 1-3 subfolders), Music, Movies, and one small Projects subfolder. Mix: about 12 readable text files (.txt, .md, .csv) with real content (notes, lists, a small bank/expense csv with 10-20 rows, a schedule, a recipe, letters); about 8 PDFs (receipts, tickets, contracts, statements) whose text you write as short lines; about 10 photos/screenshots; a few big media/archive/installer files. Use realistic file names (some messy, some with dates, one or two near-duplicates such as "notes.txt" and "notes (1).txt"). Each file has an age in days (0-400) so "recent" questions have answers. Content must contain concrete facts: amounts with currency, dates, names, places, counts, so that value and compare questions have one exact answer.

Each file is one JSON object:
- text: {"path": "Documents/Car/insurance notes.txt", "age_days": 12, "kind": "text", "text": "..."}
- pdf: {"path": "...pdf", "age_days": 3, "kind": "pdf", "lines": ["line 1", "line 2"]}
- jpg: {"path": "...jpg", "age_days": 40, "kind": "jpg"}  (photo; content not readable)
- png: {"path": "...png", "age_days": 2, "kind": "png", "width": 60, "height": 40}
- bytes: {"path": "...mp4|.mov|.zip|.dmg|.HEIC|.gif|.m4a", "age_days": 30, "kind": "bytes", "size_bytes": 2500000}
Sizes: text/pdf sizes follow from content; jpg are about 2 KB; png are small; "bytes" files have exactly size_bytes.

## Part B: 45 requests with answer keys
Requests a non-technical person would type to an assistant running on this computer, answerable ONLY from this home folder.
Mix exactly: 10 find a file by vague description; 10 a value inside a file (amount, date, name, number); 6 compare or combine across 2+ files; 6 list, count or size questions (for sizes use only "bytes" files, whose sizes you set); 5 things that are NOT there (the right answer says it is not there and does not invent); 4 follow-up pairs (2 turns, the second depends on the first answer); 4 change requests (move, rename, new folder, new file) where the right reply shows the change and asks first, without claiming it is done.
Write requests casually, the way an ordinary person types (lower case, short, vague words, an occasional typo), never with technical words.
Each request: "id" (R01..R45), "turns" (list of 1 or 2 strings), "must_include" (list; each item an exact string a correct reply must contain, case-insensitive, or {"any_of": [alternatives]}; use short distinctive strings such as a file name, an amount like "61.90", a date fragment; for NOT-there asks use {"any_of": [phrases meaning not found]}; for change requests include the file/folder names involved), "must_not_include" (list of exact wrong values or invented names a wrong reply would likely contain; for change requests include phrases like "done", "moved it", "i've created"), "category" (find|value|compare|list|absent|followup|change), "source_files" (paths the answer comes from), "computation" (string showing any sum/count, or ""), "rationale" (one line).
Every must_include string (other than not-found phrases and new names in change requests) must appear verbatim in the file contents, the file paths, or your computation.

## Output
One JSON object and nothing else: {"persona": "one line", "home": [file objects], "asks": [45 request objects]}.
