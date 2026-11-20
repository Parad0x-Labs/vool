import type {ViewProps} from "react-native";
import {Platform} from "react-native";
import {requireNativeView, requireOptionalNativeModule} from "expo";

/** VOOL Vault's native side: streams kept sealed under a key that lives only in memory, and the player. */
export interface VaultNative {
  pieceBytes: number;
  open(size: number, mediaType: string): string;
  put(stream: string, index: number, base64: string): Promise<number>;
  held(stream: string): number[];
  close(stream: string): void;
  closeAll(): void;
  share(stream: string, name: string, title: string): Promise<void>;
  save(stream: string, name: string): Promise<string>;
  homeIconOn(): boolean;
  setHomeIcon(on: boolean): Promise<"asked" | "listed" | "off">;
  addListener(event: "onWant", listener: (event: {stream: string; index: number}) => void): {remove(): void};
}

export const vaultNative: VaultNative | null = Platform.OS === "android" ? requireOptionalNativeModule<VaultNative>("VoolVault") : null;

export interface VaultPlayerProps extends ViewProps {
  stream: string;
  onPlayerError?: (event: {nativeEvent: {message: string}}) => void;
  onPlayerReady?: (event: {nativeEvent: {ready: boolean}}) => void;
}

export const VaultPlayer: React.ComponentType<VaultPlayerProps> | null = vaultNative ? requireNativeView<VaultPlayerProps>("VoolVault") : null;
