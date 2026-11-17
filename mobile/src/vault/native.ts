import type {ViewProps} from "react-native";
import {Platform} from "react-native";
import {requireNativeView, requireOptionalNativeModule} from "expo";
import type {FastCrypto} from "../protocol/fast.ts";

/** VOOL Vault's native side: streams kept sealed under a key that lives only in memory, and the player. */
export interface VaultNative {
  pieceBytes: number;
  open(size: number, mediaType: string): string;
  put(stream: string, index: number, base64: string): Promise<number>;
  held(stream: string): number[];
  close(stream: string): void;
  closeAll(): void;
  share(stream: string, name: string, title: string): Promise<void>;
  /** One share sheet for several whole streams. */
  shareMany(streams: string[], names: string[], title: string): Promise<void>;
  save(stream: string, name: string): Promise<string>;
  homeIconOn(): boolean;
  setHomeIcon(on: boolean): Promise<"listed" | "off">;
  /** A JPEG (base64) made here from the stream: a photo at about maxEdge, a video frame, a recording's cover; "" when none. */
  preview(stream: string, maxEdge: number, kind: string): Promise<string>;
  /** iPhone: the native crypto passed its built-in known answer (absent on Android, where it is always used). */
  cryptoReady?(): boolean;
  cryptoSha256Hex(text: string): string;
  cryptoHmacSha256Hex(key: string, text: string): string;
  cryptoSeal(text: string, nonceHex: string, keyHex: string): string;
  /** "" when it does not authenticate (a sealed body is never empty). */
  cryptoOpen(data: string, nonceHex: string, keyHex: string): string;
  addListener(event: "onWant", listener: (event: {stream: string; index: number}) => void): {remove(): void};
  /** iPhone: a pick from the VOOL icon's long-press menu happened (take it with takeShortcut). */
  addListener(event: "onShortcut", listener: (event: {url: string}) => void): {remove(): void};
  /** iPhone: the long-press pick not yet handled, as a VOOL link ("" when none); taken once. */
  takeShortcut?(): string;
}

export const vaultNative: VaultNative | null = Platform.OS === "android" || Platform.OS === "ios" ? requireOptionalNativeModule<VaultNative>("VoolVault") : null;

/** The link's hashing and sealing in native code, when this build has it. */
export function nativeFastCrypto(): FastCrypto | null {
  const n = vaultNative;
  if (!n || typeof n.cryptoSeal !== "function" || typeof n.cryptoOpen !== "function") return null;
  if (typeof n.cryptoReady === "function" && !n.cryptoReady()) return null;
  return {
    sha256Hex: text => n.cryptoSha256Hex(text),
    hmacSha256Hex: (key, text) => n.cryptoHmacSha256Hex(key, text),
    seal: (text, nonceHex, keyHex) => n.cryptoSeal(text, nonceHex, keyHex),
    open: (data, nonceHex, keyHex) => n.cryptoOpen(data, nonceHex, keyHex) || null,
  };
}

export interface VaultPlayerProps extends ViewProps {
  stream: string;
  onPlayerError?: (event: {nativeEvent: {message: string}}) => void;
  onPlayerReady?: (event: {nativeEvent: {ready: boolean}}) => void;
}

export const VaultPlayer: React.ComponentType<VaultPlayerProps> | null = vaultNative ? requireNativeView<VaultPlayerProps>("VoolVault") : null;
