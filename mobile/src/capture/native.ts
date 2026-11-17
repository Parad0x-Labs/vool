import type React from "react";
import {Platform, type ViewProps} from "react-native";
import {requireNativeView, requireOptionalNativeModule} from "expo";
import type {CaptureItem, CaptureVault} from "./sender.ts";

/** What the camera or recorder answers: the capture is being locked away in the background. */
export interface CaptureNote {saving: true; kind: "photo" | "video" | "sound"}

/** Sent when a capture has been locked away, or could not be. */
export type StoredEvent = ({ok: true} & CaptureItem) | {ok: false; kind: string; message: string};

interface VoolCaptureNative {
  pieceBytes: number;
  list(): Promise<CaptureItem[]>;
  readFrom(id: string, offset: number): Promise<string>;
  remove(id: string): Promise<void>;
  startSound(): Promise<void>;
  stopSound(): Promise<CaptureNote>;
  savingCount(): number;
  /** On: keep VOOL running (with a notice) while sending; false when Android refused to start it. */
  keepSending(on: boolean, text: string): boolean;
  addListener(event: "onStored", listener: (event: StoredEvent) => void): {remove(): void};
  soundRecording(): boolean;
  homeIconOn(): boolean;
  setHomeIcon(on: boolean): Promise<void>;
  canAddTile(): boolean;
  addTile(): Promise<"added" | "already" | "not_added" | "unsupported">;
  /** Optional compression (JSON strings; "" when the build has no compression package). */
  getCompressionSettings(): string;
  setCompressionSettings(json: string): string;
  getCompressionCapabilities(): string;
}

/** The private capture's native side. Android only; elsewhere the feature is simply absent. */
export const capture: VoolCaptureNative | null = Platform.OS === "android" ? requireOptionalNativeModule<VoolCaptureNative>("VoolCapture") : null;

export const vault: CaptureVault | null = capture
  ? {list: () => capture.list(), readFrom: (id, offset) => capture.readFrom(id, offset), remove: id => capture.remove(id)}
  : null;

export type CameraMode = "standard" | "night" | "hdr" | "portrait" | "retouch";

export interface CameraViewProps extends ViewProps {
  facing: "back" | "front";
  mode: CameraMode;
  video: boolean;
  onModes?: (event: {nativeEvent: {modes: CameraMode[]}}) => void;
  onCaptured?: (event: {nativeEvent: CaptureNote}) => void;
  onCameraError?: (event: {nativeEvent: {message: string}}) => void;
}

export interface CameraHandle {
  takePhoto(): Promise<CaptureNote>;
  startVideo(): Promise<boolean>;
  stopVideo(): Promise<CaptureNote>;
}

export const PrivateCameraView: React.ComponentType<CameraViewProps & {ref?: React.Ref<CameraHandle>}> | null =
  capture ? requireNativeView<CameraViewProps>("VoolCapture") as never : null;
