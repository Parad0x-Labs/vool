import type {ChatMessage, SentFile} from "../protocol/models.ts";

/** Seconds since 1970 for a message time; a time with no zone is the computer's UTC. NaN when there is none. */
export function messageSeconds(ts: unknown): number {
  if (typeof ts !== "string" || !ts.trim()) return NaN;
  const text = /[zZ]|[+-]\d\d:?\d\d$/.test(ts.trim()) ? ts.trim() : ts.trim() + "Z";
  return Date.parse(text) / 1000;
}

// The computer stamps the file a moment before it writes the answer; allow a little either side.
const BEFORE = 2;
const AFTER = 5;

/**
 * Which answer in this chat each sent file belongs to: the answer of the turn that was running when the computer
 * sent it (after the person's message, up to the answer). Files that fit no answer here are left out.
 * Answers the message index -> its files, oldest first.
 */
export function filesByAnswer(messages: ChatMessage[], files: SentFile[]): Map<number, SentFile[]> {
  const placed = new Map<number, SentFile[]>();
  const turns: {answer: number; from: number; to: number}[] = [];
  let asked = NaN;
  messages.forEach((message, index) => {
    const at = messageSeconds(message.ts);
    if (message.role === "user") {asked = at; return;}
    if (message.role !== "assistant" || Number.isNaN(at)) return;
    turns.push({answer: index, from: Number.isNaN(asked) ? at - 600 : asked, to: at});
    asked = NaN;
  });
  for (const file of [...files].sort((a, b) => a.sent_at - b.sent_at)) {
    const turn = turns.find(t => file.sent_at >= t.from - BEFORE && file.sent_at <= t.to + AFTER);
    if (!turn) continue;
    placed.set(turn.answer, [...(placed.get(turn.answer) ?? []), file]);
  }
  return placed;
}

export function isPicture(file: SentFile): boolean {
  return /^image\/(jpeg|png|gif|webp|heic|heif)$/i.test(file.media_type || "");
}

export function sizeText(bytes: number): string {
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}
