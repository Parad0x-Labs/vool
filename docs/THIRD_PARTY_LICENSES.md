# Third-Party Licenses

This project contains original code developed for VOOL, along with optional integrations that may interface with third-party software, libraries, models, runtimes, or external assets.

## Licensing Overview

### VOOL Core

Unless otherwise noted, the VOOL codebase is licensed under the MIT License (see `LICENSE`).

This includes the main product code such as:

- core orchestration
- swarm / networking logic
- storage and indexing
- meet-and-greet services
- proof / trust systems
- human input adaptation
- local runtime and service wrappers

### Public SDKs / Examples / Integration Helpers

Where explicitly marked, certain public-facing developer tools may be licensed under Apache License 2.0 to allow easier third-party integration and adoption.

These may include:

- SDKs
- client bindings
- protocol helpers
- sample integrations
- example client code

### Third-Party Dependencies

Third-party libraries, model runtimes, tokenizers, weights, and other external assets remain under their original upstream licenses.

These are not relicensed under the VOOL core license.

## Important Rule

Our license applies to our original code.

Third-party code, models, and assets keep their original licenses.

Using or interfacing with a third-party model or runtime does not change that third-party license.

## Typical Third-Party Categories

### Python Libraries

Common dependencies such as networking, cryptography, validation, ML tooling, and utility libraries remain under their own respective licenses.

Examples may include:

- Apache-2.0
- MIT
- BSD-style licenses
- other permissive licenses

### Optional Model Integrations

VOOL may support external model providers or user-supplied local models through adapters.

Examples:

- local model runtimes
- OpenAI-compatible APIs
- user-supplied model paths
- optional Transformers-based integrations
- optional open-weight model families such as Qwen

These integrations do not change the upstream license of:

- model code
- model weights
- tokenizer files
- vendor runtimes

### User-Supplied Assets

Any models, weights, tokenizers, or external files supplied by the end user remain under the user's chosen or upstream source terms.

VOOL does not claim ownership over those assets.

## Notices And Attribution

Where required by upstream licenses, this repository preserves:

- license texts
- copyright notices
- attribution notices
- NOTICE files, where applicable

These are stored in:

- `LICENSES/`
- `third_party/NOTICES/`

## No Automatic Redistribution Rights

Support for a third-party model or runtime inside VOOL does not imply that VOOL grants redistribution rights for that third-party asset.

Users are responsible for complying with the original license terms of any third-party software, model, or weight they use with VOOL.

## GPL / AGPL Boundary Policy

VOOL core should avoid directly incorporating GPL or AGPL code unless the project intentionally chooses to comply with those stronger copyleft obligations for the affected distribution.

If support is provided for GPL or AGPL tools, the preferred pattern is:

- subprocess boundary
- local service boundary
- network / API boundary

This helps keep licensing boundaries clear.

## How To Read This File

If a component was written as part of VOOL itself, assume it is covered by the project's declared license unless explicitly marked otherwise.

If a component comes from a third party, its original upstream license still applies.

If a folder or file has its own license header or separate license file, that more specific notice takes precedence.

## Maintainer Guidance

When adding a new dependency, model integration, or vendored asset:

- record the dependency name
- record the upstream source
- record the upstream license
- preserve required notices
- do not relabel third-party assets as BSL
- keep optional integrations modular where possible

## Placeholder Dependency Register

Update this section as integrations are added.

### Example Entry Format

Name: Example Dependency  
Type: Library / Model / Runtime / Asset  
Upstream: Example upstream project  
License: MIT / Apache-2.0 / etc.  
Bundled: Yes / No  
Redistributed by VOOL: Yes / No  
Notes: Optional runtime only / user-supplied / adapter-only / etc.

## Current / Planned Optional Integrations

### Qwen-family model integrations

Type: Model integration  
Bundled: No, recommended  
Redistributed by VOOL: No, recommended  
Notes: User-supplied or externally fetched under upstream terms

### Transformers-based adapter

Type: Python library integration  
Bundled: Dependency-managed  
Redistributed by VOOL: Only as dependency metadata, not relicensed  
Notes: Optional adapter path only

### OpenAI-compatible API adapters

Type: Integration adapter  
Bundled: Yes, our adapter code  
Redistributed by VOOL: Yes, our code only  
Notes: Adapter code is ours; remote provider services remain under their own terms

## Bundled Web Assets

### Chart.js 4.5.1

- path: `core/web_assets/chart-4.5.1.min.js`
- license: MIT (`core/web_assets/chart-LICENSE.md`)

### Mermaid 12.0.0

- path: `core/web_assets/mermaid-12.0.0.min.js`
- license: MIT (`core/web_assets/mermaid-LICENSE`)

## Bundled Code

### vendor/nebula-media

- path: `vendor/nebula-media/`
- license: MIT (`vendor/nebula-media/LICENSE`); origin in `vendor/nebula-media/PROVENANCE.md`
- bundled: yes, inside the macOS, Linux and Windows app bundles (the Media Studio worker)
- notes: standard library only; it runs `ffmpeg` and `ffprobe` from the computer, and neither is bundled

### Phone app: private camera settings (credit)

- path: `mobile/src/screens/CaptureScreen.tsx`, `mobile/modules/vool-capture/android/src/main/java/systems/vool/capture/VoolCameraView.kt`
- credit: the camera's settings (flash, self-timer, grid, 4:3 or 16:9, video quality, steady video, mirrored selfies, pinch to zoom, tap to focus) follow the GrapheneOS Camera app, https://github.com/GrapheneOS/Camera, MIT license, Copyright © 2021-2025 GrapheneOS
- bundled: no GrapheneOS Camera code is copied; VOOL's own code, written for CameraX. If code is taken from it later, its MIT notice goes into `LICENSES/` next to this entry

### Phone app: OpenCV 4.12.0 (camera photo modes)

- path: `mobile/modules/vool-capture/android/build.gradle` (`org.opencv:opencv:4.12.0` from Maven Central, AAR sha256 `f71846a313388d9da667a59be1e921669fcf792d1ba264b3898253ca789bc3f0`); used by `ImageEdits.kt` and `PageOutline.kt` for Enhance, Beauty and Document
- license: Apache-2.0, https://github.com/opencv/opencv/blob/4.12.0/LICENSE
- bundled: yes, `libopencv_java4.so` and `libc++_shared.so` for arm64-v8a only. The app ships arm64 only (`app.json` excludes `lib/x86/**`, `lib/x86_64/**`, `lib/armeabi-v7a/**`); OpenCV's x86 builds link Intel IPP-ICV, which has its own license and is therefore never shipped
- built into the arm64 library (from its own build report): libjpeg-turbo 3.1.0 (IJG + BSD-3-Clause + zlib), libpng 1.6.43 (libpng license), libtiff 4.6.0 (libtiff license), libwebp (BSD-3-Clause), OpenJPEG 2.5.3 (BSD-2-Clause), OpenEXR 2.3.0 (BSD-3-Clause), zlib (zlib), protobuf 3.19.1 (BSD-3-Clause), flatbuffers 23.5.9 (Apache-2.0), oneTBB 2022.1 (Apache-2.0), ittnotify (BSD-3-Clause), ade (Apache-2.0), carotene (BSD-3-Clause), KleidiCV 0.5.0 (Apache-2.0), cpufeatures (BSD-2-Clause). No FFmpeg, no non-free modules
- notes: GPUPixel, android-gpuimage-plus and Google ML Kit are not used. Photo modes run on the phone, on the private working copy, before compression and sealing

## Disclaimer

This document is an operational licensing guide for the repository structure.

It is not legal advice.
