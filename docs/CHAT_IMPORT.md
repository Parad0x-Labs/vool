# Bring in chats and working context

Open **Settings → Backup & restore → Bring your chats from other AI apps**. Choose a local source or an export archive, review its chats, and select the ones to import. Source files stay unchanged. Import sends nothing online and never starts source commands.

Picked archives stay in memory. The data directory holds only an empty opaque receipt; ignored configuration members are not staged on disk. After a restart, choose the archive again. Forgotten text is also erased from interrupted import titles before recovery.

Imported history contains visible user and assistant messages, titles and recorded dates. Other branches, reasoning and tool messages are excluded. Secret text is masked. Attachments retain display names; their contents are not copied.

For each chat, expand **Bring working context**, choose an existing local folder, and review it. If the folder moved, enter its previous path. Supply additional instructions or memories when the export omits them. Review lists available, missing, outside and refused file references; an old path never grants access outside the chosen folder. Editing these choices requires another review.

Choose each category explicitly:

- Instructions become project or chat RULES through the normal rule owner. Verified source precedence is preserved. Only unconditional root editor rules become active instructions; conditional, manual and nested editor rules are saved as inactive local drafts with their original conditions.
- Reviewed memories, including date-named daily notes, use the public memory writer, source labels and local scope. Mark private chats during review; private imports cannot create RULES or saved memories.
- Skills become local chat drafts. Review and activate them through the existing skill tools; import never publishes or activates a skill.
- Supported local tool definitions become disabled entries. Credentials and environment values are omitted. Review the saved definition and configure it through the existing local tool configuration before explicitly enabling it. HTTP tool transports are unsupported and reported as omitted.
- Supported schedules become paused records retaining their prompt and timing. Foreign schedule conversion and resume are not automatic. Create a native task after reviewing its prompt and timing. Import does not recreate foreign delivery authority.
- Recorded model/provider names are shown without credentials. Select an existing model on this device; add any needed credentials separately in Settings.

Each chat gets its own inactive copies of drafts, tools and tasks. Identical project rules are deduplicated by their canonical owner. The durable import overview shows saved resources after reload, reading current owners rather than caching source prose. Later credentials are omitted from this overview.

**Use this history with a local model when I continue these chats** is off initially. Opting in includes authorized imported history on later turns. Imported working context and opted-in history restrict inference and fallback to configured loopback models on this device; other devices and cloud models are refused. Existing file/tool permissions and context budgets still apply. At most the newest 2,000 imported messages are read for a turn. Default history-only imports remain readable without becoming model context or saved memory.

Reviewed file references and settings reach the model as source data, separately from the optional transcript. Internal receipt identifiers and inactive resource definitions are excluded. The data is re-read and checked against erasure authority on each turn.

Continuation checks erasure authority every turn. Forgotten content stays withheld after undo and reimport. Missing authority refuses access. If a queued chat moves to another project, the turn stops and asks you to resend. An incomplete context transfer cannot be served; undo it before importing again.

**Undo import** removes the batch's owned additions and chats through their normal owners. It preserves existing project metadata and source files. Later conversation turns, edited/activated resources, or another batch using the project can make undo refuse to preserve that work. Undo the later batch first when appropriate. Interrupted deletion keeps a durable receipt for retry; success requires owner, transcript, namespace and routing-telemetry cleanup.

The two supported account export layouts and three verified local history layouts support explicit folder/context recovery. Native desktop caches, closed account/project settings and unknown source schemas are not inferred. Unsupported partitions, unreadable files and bounded scans produce a visible warning. A folder review never traverses above its selected root; source directory activation semantics without a native equivalent require manual draft review.

Local database readers copy stable database, write-ahead-log and rollback-journal bytes into owner-only scratch, then use SQLite's backup API on the private copy. Source databases are never opened for writing. The final snapshot is read-only and scratch is removed after reading. Corrupt, oversized, unstable and unsupported stores are reported instead of guessed at.
