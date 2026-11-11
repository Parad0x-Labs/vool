// Whether the computer is answering right now. Only an authenticated (sealed) reply counts as
// "connected"; a network failure or a reply that cannot be verified counts as "offline".
export type LinkState = "connecting" | "connected" | "offline";

let current: LinkState = "connecting";
const listeners = new Set<(state: LinkState) => void>();

export function linkState(): LinkState {return current;}
export function subscribeLink(listener: (state: LinkState) => void): () => void {
  listeners.add(listener);
  return () => {listeners.delete(listener);};
}
function set(next: LinkState) {
  if (next === current) return;
  current = next;
  for (const listener of [...listeners]) listener(next);
}
export function reportLink(answered: boolean) {set(answered ? "connected" : "offline");}
export function resetLink() {set("connecting");}
