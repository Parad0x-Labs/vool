# See what leaves your computer

Planned for v0.7.4, not released yet.

When a chat first sends files or remembered information to a cloud model, a preview shows the
complete request and its destination. Sensitive content and project rules can also require a
preview. Sending checks the same request bytes you reviewed. A changed request needs a new review.

You can send it as shown, select parts, replace names and details, or stop without sending.
A local model is offered when an installed, verified model can handle the request. Choosing it
starts a local attempt with the original task; it cannot fall back to a cloud model.

“Replace names and details” runs on this computer. It can replace recognized email addresses,
phone numbers, account and card numbers, street addresses and names from your contacts. Matching
placeholders in the answer are restored locally. This is best effort: check the preview.
Unrecognized formats and pictures may still contain personal information.

In Settings > Privacy & Permissions, use “Before files go to cloud” to choose previews and review
project cloud rules. Rules can keep sources on this computer, ask each time, or allow cloud use.
Review and confirm rule changes before they apply. Once a project has rules, sources without a
matching rule stay on this computer. Documents cannot confirm or widen these rules.

“See what was sent” opens the exact request available in the current process for up to five
minutes. Saving copies is unavailable in this version. Forget and chat deletion invalidate these
live previews. Older answers may therefore have no request available to view or compare.

From that view, “Try this with another model” compares the prepared input with up to two compatible
models you have configured. Each cloud request requires its own preview. Existing spending limits
still apply. The view reports measured response times, tokens and billed costs where available;
unknown charges remain unknown. Choosing an answer saves only a local preference and does not
change model routing.

Configured command models can also compare when they explicitly accept complete chat requests.
Older commands that accept only one prompt are unavailable for this action: the conversation's
roles, files and settings must be preserved. A buffered answer has no measured first-token time.

Local-only mode and disabled web access retain their existing restrictions. Web searches, email
and other connected services have their own destinations and controls. These previews do not
prove that every kind of online activity has been reviewed, or that malicious documents are harmless.
