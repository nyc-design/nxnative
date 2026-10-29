# Apple Vision Pro (experimental)

`--platform visionos` builds a game as a native visionOS app: the same host, rewritten game code and Metal renderer as the iPhone app, shown in a 16:9 window you place in your space. It is not immersive and not stereoscopic. The port follows [MacNativeGames](https://github.com/ly05010419/MacNativeGames)' visionOS target.

**Status.** CI compiles every iOS host source for the visionOS SDK and links the unsigned Vision Pro app from the production project (the `visionos-build` job in `.github/workflows/tests.yml`, with synthetic game modules). No game has been run on a Vision Pro yet. Send back `Documents/log.txt` (below) from the first runs.

## What you need

- An Apple-silicon Mac with macOS 26.6 or later and Xcode 27.1, with **visionOS** in Xcode → Settings → Components (or `xcodebuild -downloadPlatform visionOS`), and the rest of [README's tool install](../README.md#build-from-the-command-line): the Metal toolchain, `xcodegen zstd glslang`, the .NET SDK and the Python packages.
- An Apple Vision Pro with **Developer Mode** on (Settings → Privacy & Security → Developer Mode, after Xcode has seen it once), paired with the Mac: Vision Pro Settings → General → Remote Devices, then Xcode → Window → Devices and Simulators. The Mac and the headset must be on the same network.
- An Apple account in Xcode → Settings → Apple Accounts. A free personal team works, with two limits: apps expire after 7 days (run again from Xcode), and it cannot sign the Increased Memory Limit capability (see [Signing](#signing)).
- A game controller paired with the Vision Pro (Settings → Bluetooth). The touch controls of the iPhone app are drawn, but pinching them is no substitute for a controller.
- Your own game dump and `prod.keys`. For **Super Mario Odyssey** (profile `0100000000010000`) the folder holds the base game and update 1.4.1 (v393216), the only version with a profile:

  ```
  ~/Games/Odyssey/
    Super Mario Odyssey [0100000000010000][v0].nsp
    Super Mario Odyssey [0100000000010800][v393216].nsp
  ```

Check the toolchain first; every line should print a version:

```
xcodebuild -version
xcrun --sdk xros --show-sdk-version
xcrun --sdk xros metal --version
which xcodegen
```

## Build and install

In one command, with Xcode signed in to your Apple ID and the headset paired and awake: `python3 scripts/deploy.py ~/Games/Odyssey -k ~/keys/prod.keys --device vision --bundle-prefix com.<you>.nxnative` prepares, builds, signs, installs, copies the game files and launches the game ([deploy.py](development.md#one-command-deploypy)). The headset has no cable but the Developer Strap, so the game files go over Wi-Fi; on a slow network add `--no-push-romfs` and AirDrop the image instead. By hand:

1. Prepare the project (the first run takes 30–60 minutes and needs about twice the game's size plus 10 GB free on the game's drive):

   ```
   python3 scripts/build_ipa_from_nsp.py ~/Games/Odyssey -k ~/keys/prod.keys --platform visionos --romfs separate
   ```

   `--platform visionos` implies `--prepare-xcode` and keeps the extracted files. `--romfs separate` leaves the game files out of the app, so each install sends only the code and shaders. The last lines print the workspace, the bundle ID and the `push_romfs.sh` command for step 4.
2. Open `~/Games/NxNative.xcworkspace` (beside the NSP folder). Select the scheme **Super Mario Odyssey (0100000000010000) Vision Pro**. Under the **DmIOS** target's **Signing & Capabilities**, select your team.
3. Choose your Apple Vision Pro as the run destination, then **Run**. The scheme runs **Release**. The first build compiles the host (several minutes).
4. With the app installed, copy the game files once per game version. Find the headset's identifier with `xcrun devicectl list devices`, then run the command step 1 printed:

   ```
   scripts/push_romfs.sh <device identifier> org.nxnative.0100000000010000.visionos "<NSP folder>/0100000000010000_build/extract/<version>/RomFS.img"
   ```

5. Open the app on the headset. The first start compiles the game's GPU pipelines, so expect a slow start with stutter the first time each area loads.

A game's Vision Pro build keeps its own folders in `<title ID>_build`: `shader/visionos` (the `xros` libraries), `xcode-visionos` and `xcode-derived-data-visionos`, and its default bundle ID is `org.nxnative.<title ID>.visionos`. The extraction, the rewritten code and the Mac shader cache are shared. So the same NSP folder can also build the iPhone IPA or iPhone Xcode project (without `--platform visionos`), before or after, and each reuses what the other made; the shader translation, which is done per device, runs again. Run one build at a time per NSP folder: they share those stages' outputs.

## Signing

The project asks for the Increased Memory Limit capability, as the iPhone one does. A free personal team cannot provision it, and Xcode then stops with a provisioning error. Prepare again with the capability left out:

```
DM_ENTITLEMENTS= python3 scripts/build_ipa_from_nsp.py ~/Games/Odyssey -k ~/keys/prod.keys --platform visionos --romfs separate
```

Without it visionOS may close the game when it runs short of memory. If that happens (the app vanishes, often while loading a kingdom), a paid developer account is the way to keep the capability.

## Diagnostics

The app writes `Documents/log.txt`. To copy it to the Mac:

```
xcrun devicectl device copy from --device <device identifier> --domain-type appDataContainer --domain-identifier <bundle ID> --source Documents/log.txt --destination log.txt
```

`Documents/env.txt` takes the same switches as on the iPhone (one `NAME=value` per line, for example `DM_SHOW_FPS=1`; copy it with `devicectl device copy to`).

## What differs from the iPhone app

[`dm/ios/tools/apple_platform.py`](../dm/ios/tools/apple_platform.py) holds every build difference, selected by `DM_APP_PLATFORM=visionos` (set by `--platform visionos`; `build_ipa.sh`, `compile_check.py` and `build_smoke.py` read it):

- The project template's `platform: visionOS`, deployment target visionOS 26, device family 7, `-mcpu=apple-a15` (the M2's core generation, which has the LSE atomics the host assembly needs), no `UIRequiresFullScreen`. A template change that drops one of these lines stops the build instead of producing an iPhone app.
- No IOKit in the link line (the host uses it only on the Mac).
- Shader libraries for the `xros` SDK ([`ios_metal.py`](../dm/ios/tools/ios_metal.py), `vk_shaders.py`, `gl_shaders.py`). The SDK is part of the library cache keys, so iPhone and headset libraries never mix.
- Its own shader folder, Xcode project, derived data, scheme name (`… Vision Pro`) and bundle ID (`….visionos`), so it sits beside the game's iPhone build.
- No offline pipeline archive: it is compiled for iPhone GPUs.
- No app icon: visionOS icons are layered image stacks.

In the sources (`TARGET_OS_VISION`):

- `main.m` asks for a 1280×720 window that resizes in proportion.
- `front_ios.m` takes the drawable scale from the view's trait collection once it is in a window, sends controller events to GameController (`GCEventInteraction`), and reports no screen brightness.
- `DmGamepad.m` has no touch-control feedback (visionOS has no feedback generators) and no device motion.
- `nvn_mtl.m` reports a 90 Hz display (visionOS has no `UIScreen`).

## Open risks

- No device run yet: Metal 4 behaviour on the M2 GPU, frame pacing at 90 Hz and memory limits are unmeasured.
- The shader libraries are compiled for the SDK's visionOS version (no minimum-version flag for `xros`): keep the headset on the current visionOS.
- No headset motion: shake throws and motion aiming come only from a controller with motion sensors (a DualSense or Joy-Con, not an Xbox controller).
