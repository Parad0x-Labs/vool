import {randomBytes, toHex} from "./sha256.ts";

export interface PaymentReview {review_token:string; amount:string; asset:string; network:string; destination:string; warnings:string[]; expires_at:number}
export interface ProposalClient {previewPayment(value:SavedProposal):Promise<PaymentReview>; proposePayment(value:SavedProposal & {review_token:string;warnings_acknowledged:string[]}):Promise<unknown>}
export interface SavedProposal {destination: string; amount: string; idempotency_key: string}
export interface ProposalStore {load(): Promise<SavedProposal | null>; save(value: SavedProposal): Promise<void>; clear(expectedKey?: string): Promise<void>}
export class MemoryProposalStore implements ProposalStore {
  private value: SavedProposal | null = null;
  async load() {return this.value;}
  async save(value: SavedProposal) {this.value = value;}
  async clear(expectedKey?: string) {if (!expectedKey || this.value?.idempotency_key === expectedKey) this.value = null;}
}

/** No automatic payment retries. Save the proposal identity before any packet. */
export class PaymentDraft {
  private active: Promise<unknown> | null = null;
  private store: ProposalStore;
  private client: ProposalClient;
  private confirm:(review:PaymentReview)=>Promise<boolean>;
  constructor(store: ProposalStore, client: ProposalClient, confirm:(review:PaymentReview)=>Promise<boolean>) {this.store=store;this.client=client;this.confirm=confirm;}
  load() {return this.store.load();}
  discard() {if (this.active) throw new Error("Wait for this request to finish."); return this.store.clear();}
  send(destination: string, amount: string): Promise<unknown> {
    if (this.active) return this.active;
    const work = async () => {
      let value = await this.store.load();
      if (value && (value.destination !== destination || value.amount !== amount)) {
        throw new Error("A saved payment request still needs a check on your computer. Discard it before changing the amount or payee.");
      }
      if (!value) {
        if (!destination.trim() || destination.length > 256 || !/^\d+(?:\.\d+)?$/.test(amount) || amount.length > 64 || !/[1-9]/.test(amount)) throw new Error("Enter a payee and an exact positive amount.");
        value = {destination, amount, idempotency_key: toHex(randomBytes(24))};
        await this.store.save(value);
      }
      const review = await this.client.previewPayment(value);
      if (!await this.confirm(review)) throw Object.assign(new Error("Payment review cancelled."), {code:"review_cancelled"});
      const result = await this.client.proposePayment({...value, review_token:review.review_token, warnings_acknowledged:review.warnings});
      await this.store.clear(value.idempotency_key);
      return result;
    };
    this.active = work().finally(() => {this.active = null;});
    return this.active;
  }
}
