#!/usr/bin/env python3
"""The Apple device platform the app is built for: iOS (iPhone and iPad, the default) or visionOS (Apple Vision Pro, experimental).

DM_APP_PLATFORM=visionos selects visionOS; unset, empty or ios selects iOS. The project template (dm/ios/project.yml) is written for iOS;
project_for_platform() rewrites the few lines that differ, and fails when the template no longer has them, so a template change cannot silently drop them.
The shader chain's SDK choice lives in ios_metal.py (a chain file, kept free of imports) and follows the same variable.
Usage from a shell: apple_platform.py <name | sdk | destination | products | project <project.yml>>
"""
import os
import sys

# name: (xcrun SDK, xcodebuild destination, Release products folder suffix, clang flags of the device target)
PLATFORMS = {
    'ios': ('iphoneos', 'generic/platform=iOS', 'iphoneos', ['-arch', 'arm64', '-miphoneos-version-min=26.0', '-mcpu=apple-a18']),
    # Vision Pro: M2 (2024) or M5 (2025). apple-a15 is the M2's core generation and still has the LSE atomics the host assembly uses
    'visionos': ('xros', 'generic/platform=visionOS', 'xros', ['-target', 'arm64-apple-xros26.0', '-mcpu=apple-a15']),
}

# (iOS template line, visionOS line); an empty replacement removes the line
VISIONOS_PROJECT = (
    ('    iOS: "26.0"', '    visionOS: "26.0"'),
    ('    platform: iOS\n', '    platform: visionOS\n'),
    ('TARGETED_DEVICE_FAMILY: "1,2"', 'TARGETED_DEVICE_FAMILY: "7"'),   # 7 = Apple Vision
    ('OTHER_CFLAGS: $(inherited) -mcpu=apple-a18', 'OTHER_CFLAGS: $(inherited) -mcpu=apple-a15'),
    ('        UIRequiresFullScreen: true\n', ''),   # iOS-only multitasking key
    # A visionOS window reports portrait. With only the landscape orientations listed, presenting the launcher's UIHostingController threw
    # UIApplicationInvalidInterfaceOrientation ("no common orientation with the application") at start on Apple Vision Pro
    ('          - UIInterfaceOrientationLandscapeLeft\n', '          - UIInterfaceOrientationLandscapeLeft\n          - UIInterfaceOrientationPortrait\n'),
)

# Frameworks the host's Mac link line names that visionOS does not have; the code that uses them is Mac-only (!TARGET_OS_IPHONE)
VISIONOS_NO_FRAMEWORKS = {'IOKit'}


def name(env=None):
    p = (env if env is not None else os.environ).get('DM_APP_PLATFORM') or 'ios'
    if p not in PLATFORMS: raise ValueError(f'DM_APP_PLATFORM must be ios or visionos, not {p!r}')
    return p


def sdk(env=None): return PLATFORMS[name(env)][0]
def destination(env=None): return PLATFORMS[name(env)][1]
def products(env=None): return 'Release-' + PLATFORMS[name(env)][2]
def clang_flags(env=None): return list(PLATFORMS[name(env)][3])


def frameworks(fws, env=None):
    return [f for f in fws if not (name(env) == 'visionos' and f in VISIONOS_NO_FRAMEWORKS)]


def project_for_platform(text, env=None):
    if name(env) == 'ios': return text
    for before, after in VISIONOS_PROJECT:
        if before not in text: raise ValueError(f'visionOS: dm/ios/project.yml no longer has {before.strip()!r}; update apple_platform.VISIONOS_PROJECT')
        text = text.replace(before, after)
    return text


if __name__ == '__main__':
    try:
        cmd = sys.argv[1] if len(sys.argv) > 1 else ''
        if cmd == 'project' and len(sys.argv) == 3:
            with open(sys.argv[2]) as f: text = f.read()
            text = project_for_platform(text)
            with open(sys.argv[2], 'w') as f: f.write(text)
        elif cmd in ('name', 'sdk', 'destination', 'products'): print(globals()[cmd]())
        else: sys.exit('usage: apple_platform.py <name | sdk | destination | products | project <project.yml>>')
    except ValueError as e: sys.exit(str(e))
