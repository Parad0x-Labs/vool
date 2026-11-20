import type React from "react";
import {Platform, type ViewProps} from "react-native";
import {requireNativeView, requireOptionalNativeModule} from "expo";
import type {CaptureItem, CaptureVault, GoneNote} from "./sender.ts";

/** What the camera or recorder answers: the capture is being locked away in the background. */
export interface CaptureNote {saving: true; kind: "photo" | "video" | "sound"}

/** Sent when a capture has been locked away, or could not be. */
export type StoredEvent = ({ok: true} & CaptureItem) | {ok: false; kind: string; message: string};

interface VoolCaptureNative {
  pieceBytes: number;
  list(): Promise<CaptureItem[]>;
  readFrom(id: string, offset: number): Promise<string>;
  remove(id: string): Promise<void>;
  gone(): Promise<GoneNote[]>;
  goneRecorded(sha256: string): Promise<void>;
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
  /** The VOOL Camera icon or tile opened VOOL a moment ago: the camera opens without VOOL's unlock. */
  cameraLaunchRecent(): boolean;
  clearCameraLaunch(): void;
  getCameraPrefs(): string;
  setCameraPrefs(json: string): void;
  photoSizes(facing: "back" | "front"): Promise<{sizes: string[]; sensorMax: string}>;
}

/** The private capture's native side. Android only; elsewhere the feature is simply absent. */
export const capture: VoolCaptureNative | null = Platform.OS === "android" ? requireOptionalNativeModule<VoolCaptureNative>("VoolCapture") : null;

export const vault: CaptureVault | null = capture
  ? {list: () => capture.list(), readFrom: (id, offset) => capture.readFrom(id, offset), remove: id => capture.remove(id),
      gone: () => capture.gone(), goneRecorded: sha256 => capture.goneRecorded(sha256)}
  : null;

export type CameraMode = "standard" | "night" | "hdr" | "portrait" | "retouch";

export type FlashMode = "off" | "auto" | "on";
export type PhotoFilter = "none" | "warm" | "cool" | "vivid" | "fade" | "mono" | "noir";
export const PHOTO_FILTERS: PhotoFilter[] = ["none", "warm", "cool", "vivid", "fade", "mono", "noir"];

export interface CameraViewProps extends ViewProps {
  facing: "back" | "front";
  mode: CameraMode;
  video: boolean;
  flash?: FlashMode;
  aspect?: "4:3" | "16:9";
  /** "max" or "WIDTHxHEIGHT" from photoSizes(). */
  photoSize?: string;
  videoQuality?: "highest" | "2160" | "1080" | "720";
  steady?: boolean;
  mirror?: boolean;
  filter?: PhotoFilter;
  onZoom?: (event: {nativeEvent: {ratio: number; min: number; max: number}}) => void;
  onFocus?: (event: {nativeEvent: {x: number; y: number}}) => void;
  /** How the phone is held: 0, 90, 180 or 270. Photos and videos are saved the same way round. */
  onTurn?: (event: {nativeEvent: {degrees: number}}) => void;
  onModes?: (event: {nativeEvent: {modes: CameraMode[]}}) => void;
  onCaptured?: (event: {nativeEvent: CaptureNote}) => void;
  onCameraError?: (event: {nativeEvent: {message: string}}) => void;
}

export interface CameraHandle {
  takePhoto(): Promise<CaptureNote>;
  startVideo(): Promise<boolean>;
  stopVideo(): Promise<CaptureNote>;
  setZoom(ratio: number): Promise<void>;
}

export const PrivateCameraView: React.ComponentType<CameraViewProps & {ref?: React.Ref<CameraHandle>}> | null =
  capture ? requireNativeView<CameraViewProps>("VoolCapture") as never : null;
