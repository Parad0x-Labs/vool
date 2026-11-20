import {filesByAnswer, messageSeconds} from "../../src/chat/sentFiles.ts";
import type {ChatMessage, SentFile} from "../../src/protocol/models.ts";

const file = (id: string, at: number): SentFile => ({id, name: id + ".jpg", media_type: "image/jpeg", size_bytes: 1000, sha256: "", sent_at: at, expires_at: at + 86400});
const t = (s: number) => new Date(s * 1000).toISOString();

describe("a file the computer sent shows in the answer that sent it", () => {
  const base = 1_800_000_000;
  const messages: ChatMessage[] = [
    {role: "user", content: "what's in Downloads", ts: t(base)},
    {role: "assistant", content: "Newest in Downloads: ...", ts: t(base + 4)},
    {role: "user", content: "send me the latest picture", ts: t(base + 60)},
    {role: "assistant", content: "Sent a.jpg", ts: t(base + 63)},
  ];

  test("placed in its own answer, not an earlier one", () => {
    const placed = filesByAnswer(messages, [file("a", base + 62)]);
    expect([...placed.keys()]).toEqual([3]);
  });

  test("a file stamped a moment after the answer still belongs to it", () => {
    expect([...filesByAnswer(messages, [file("a", base + 65)]).keys()]).toEqual([3]);
  });

  test("a file sent outside every answer here is left for the list", () => {
    expect(filesByAnswer(messages, [file("x", base - 3600), file("y", base + 4000)]).size).toBe(0);
  });

  test("an answer with no time takes no file", () => {
    expect(filesByAnswer([{role: "user", content: "hi"}, {role: "assistant", content: "Sent"}], [file("a", base)]).size).toBe(0);
  });

  test("a time without a zone is read as UTC", () => {
    expect(messageSeconds("2026-11-20T22:40:00")).toBe(messageSeconds("2026-11-20T22:40:00Z"));
    expect(Number.isNaN(messageSeconds(undefined))).toBe(true);
  });
});
