# Experimental native visionOS support

This branch adds an experimental native Apple Vision Pro target to [nxnative](https://github.com/seedds/nxnative), referencing [MacNativeGames](https://github.com/ly05010419/MacNativeGames)' visionOS port.

**Status: Source patched; not yet compiled using Xcode or tested on Vision Pro.** Odyssey gameplay, Metal rendering and signing entitlements are not confirmed.

## Requirements

- Apple-silicon Mac, Xcode with **visionOS SDK** and Metal Toolchain, plus nxnative's usual documented build dependencies.
- Vision Pro in Developer Mode, paired with Xcode, configured Apple development signing, and a physical controller.
- Your own dumped Super Mario Odyssey base NSP/XCI and matching update (preferably tested v1.4.1), plus your own `prod.keys`. Do not commit game files or keys.

## Before starting shader compilation

```bash
xcodebuild -version
xcrun --sdk xros --show-sdk-version
xcrun --sdk xros metal --version
xcrun --sdk macosx metal --version
which xcodegen
```

All commands should succeed. The upstream project pins an Xcode Metal compiler; using a different version may trigger its existing compiler compatibility check.

## Prepare the Xcode project for Odyssey

```bash
DM_APP_PLATFORM=visionos python3 scripts/build_ipa_from_nsp.py \
  ~/Games/Odyssey \
  -k ~/keys/prod.keys \
  --prepare-xcode --romfs separate --keep-extracted --gpu none
```

The `--romfs separate` option avoids repeatedly copying the large RomFS into the signed app. The tool prints the workspace and Xcode project paths. Open the project, choose your **Team** and your physical **Apple Vision Pro** under destinations, and Run using the **Release** scheme.

After installing the app, transfer the game data from the Mac (the game-preparation script prints a concrete command):

```bash
bash scripts/push_romfs.sh "<VISION_PRO_UDID>" "<BUNDLE_ID>" "<PATH_TO_ROMFS.img>"
```

Then relaunch. The RomFS is not downloaded or distributed by this repository.

## Implementation

- Native visionOS Xcode target (`platform: visionOS`, device family `7`, visionOS 26) generated from the per-game iOS template.
- Baseline `-mcpu=apple-a15` instead of iPhone A18 to work with an Apple M2 CPU.
- `xros` Metal shader libraries for NVN, Vulkan and OpenGL with SDK-aware shader cache identities.
- A regular, 16:9 visionOS window (`1280×720`) with `CAMetalLayer` rendering.
- GameController event capture through `GCEventInteraction`.
- Guards around phone-only screen/brightness/orientation/motion APIs.
- Device resource-copy phase accepts `xros`. The original iOS build path remains unchanged.
- iPhone-specific offline GPU pipeline archives are disabled for Vision Pro; shader compilation may occur during the first launch.

## Limitations

This is **windowed native visionOS**, not fully immersive VR or stereoscopy. A Vision Pro device build and real runtime validation still need to be done. Common remaining risks: unsupported APIs in linked host sources, Metal 4 shader/compiler differences, requested increased-memory-limit entitlement, device-memory pressure and game-specific graphics or control issues.

Keep the first Xcode build logs and the application's `Documents/log.txt` when troubleshooting.
