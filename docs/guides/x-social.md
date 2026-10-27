# X drafts, ratings and results

Connect your own X account in Settings and set a monthly read and action limit. Reads use that account's API allowance. Each proposed post, reply, quote or other account action waits for your approval in X activity. A proposal from the local desktop chat also appears below its originating turn. Approving either view sends the exact frozen action once; declining sends nothing. An expired proposal needs a fresh proposal. If X's response is lost, check X before trying again.

Choose a rating from 1 to 10 and optionally add a comment beside a pending or sent post. The next X drafting request receives a small selection of your highest and lowest ratings for its project, with global guidance as a fallback. Ratings guide drafting; they do not change a model or prove that a post will perform well. A rating of 9 or 10 offers a separate **Use as a voice sample** button. Saving the rating alone does not add a voice sample.

The ratings panel lets you pause guidance, forget one rating, or reset its scope. Forget removes the rating-store entry and any voice sample added from that rating. Full erasure of copies in chat history, tool traces and prompt records requires the separate memory integration and security review; this build does not establish that guarantee. Avoid treating the current panel as a history-erasure control.

`x.feedback.save` accepts only a score explicitly typed in the current user turn and the user's verbatim comment. It cannot infer a rating. `x.feedback.list` provides bounded guidance for the current project and global scope.

`x.results` observes your own posts. It stores post identifiers, capture times, post ages and numerical metrics. Missing metrics are marked **not available**. A single snapshot is an observation; comparisons require equal post ages. A confirmed deleted post loses its saved snapshots on the next read. Rate limits stop reads and report the reset time when X supplies it.

`x.replies.unanswered` reads recent comments on your own post and excludes replies already answered by you or covered by a local action. Its coverage is limited to the returned search window and pages; incomplete own-reply coverage refuses to label comments unanswered. Draft and propose a separate reply for each comment. Every reply still needs its own approval.

An installed local skill may declare the X and note tools it needs in `allowed-tools`. Matched, available tools receive bounded offer seats; declarations grant no permission and enable no connection, network access, usage allowance or send. Known optional X/note tools absent or disabled on the runtime are reported as unavailable during validation and installation. Unknown tool names still refuse validation. The ordinary plain-text catalog flag stays off.

Phone approval and automatic posting are future work. Automatic posting requires the owner's explicit instruction and X's written approval. No automatic posting mode is included here.
