"""The Apple Vision Pro variant (DM_APP_PLATFORM=visionos): the project template rewrite, the SDK the shader chain compiles for, the frameworks and the NSP script's switch."""
import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
for folder in ('scripts', 'dm/ios/tools'):
    sys.path.insert(0, str(REPO / folder))
import apple_platform


class ApplePlatformTests(unittest.TestCase):
    def setUp(self):
        self.template = (REPO / 'dm/ios/project.yml').read_text()

    def test_ios_is_the_default_and_unchanged(self):
        for env in ({}, {'DM_APP_PLATFORM': ''}, {'DM_APP_PLATFORM': 'ios'}):
            with self.subTest(env=env):
                self.assertEqual(apple_platform.name(env), 'ios')
                self.assertEqual(apple_platform.sdk(env), 'iphoneos')
                self.assertEqual(apple_platform.products(env), 'Release-iphoneos')
                self.assertEqual(apple_platform.project_for_platform(self.template, env), self.template)
                self.assertIn('IOKit', apple_platform.frameworks(['Metal', 'IOKit'], env))

    def test_visionos_project(self):
        env = {'DM_APP_PLATFORM': 'visionos'}
        text = apple_platform.project_for_platform(self.template, env)
        self.assertIn('    visionOS: "26.0"', text)
        self.assertIn('    platform: visionOS\n', text)
        self.assertIn('TARGETED_DEVICE_FAMILY: "7"', text)
        self.assertIn('-mcpu=apple-a15', text)
        for gone in ('    iOS: "26.0"', '    platform: iOS\n', 'UIRequiresFullScreen', '-mcpu=apple-a18', '"1,2"'):
            self.assertNotIn(gone, text)
        self.assertEqual((apple_platform.sdk(env), apple_platform.destination(env), apple_platform.products(env)),
                         ('xros', 'generic/platform=visionOS', 'Release-xros'))
        self.assertEqual(apple_platform.frameworks(['Metal', 'IOKit', 'GameController'], env), ['Metal', 'GameController'])

    def test_template_drift_fails(self):
        with self.assertRaisesRegex(ValueError, 'TARGETED_DEVICE_FAMILY'):
            apple_platform.project_for_platform(self.template.replace('TARGETED_DEVICE_FAMILY: "1,2"', 'TARGETED_DEVICE_FAMILY: "1"'), {'DM_APP_PLATFORM': 'visionos'})

    def test_unknown_platform_fails(self):
        with self.assertRaises(ValueError): apple_platform.name({'DM_APP_PLATFORM': 'tvos'})

    def test_shader_chain_sdk_follows_the_platform(self):
        import ios_metal
        try:
            for value, sdk in ((None, 'iphoneos'), ('visionos', 'xros')):
                with self.subTest(platform=value), patch.dict(os.environ, {}, clear=False):
                    os.environ.pop('DM_APP_PLATFORM', None)
                    if value: os.environ['DM_APP_PLATFORM'] = value
                    m = importlib.reload(ios_metal)
                    self.assertEqual(m.SDK, apple_platform.sdk())
                    self.assertEqual(m.SDK, sdk)
                    self.assertIn(f'-sdk {sdk} metal', m.RECORD)   # the record is in the library cache keys: iPhone and headset libraries never mix
        finally:
            importlib.reload(ios_metal)


class NspScriptTests(unittest.TestCase):
    def setUp(self):
        import build_ipa_from_nsp
        self.build = build_ipa_from_nsp

    def main(self, *argv):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('DM_APP_PLATFORM', None)
            with self.assertRaises(self.build.BuildError) as e: self.build.main(self.build.parse_args(['/nonexistent-nsp', '-k', '/nonexistent.keys', *argv]))
            return str(e.exception), os.environ.get('DM_APP_PLATFORM')

    def test_platform_visionos_switches_the_device_platform(self):
        self.assertEqual(self.build.parse_args(['nsp', '--platform', 'visionos']).platform, 'visionos')
        message, platform = self.main('--platform', 'visionos')
        self.assertEqual(platform, 'visionos')   # what build_ipa.sh and the shader tools read
        self.assertNotIn('visionos', message)   # past the platform checks: it stops at the missing NSP folder

    def test_platform_visionos_needs_the_extracted_files(self):
        message, _ = self.main('--platform', 'visionos', '--no-keep-extracted')
        self.assertIn('--no-keep-extracted', message)

    def test_visionos_is_not_a_mac_build(self):
        with patch.dict(os.environ, {'DM_APP_PLATFORM': 'visionos'}):
            with self.assertRaisesRegex(self.build.BuildError, 'mac'):
                self.build.main(self.build.parse_args(['/nonexistent-nsp', '-k', '/nonexistent.keys', '--platform', 'mac']))

    def test_layer_shader_count_reads_the_headset_sdk(self):
        self.assertIn("r'(?:iphoneos|xros): (\\d+)'", Path(self.build.__file__).read_text())

    def test_missing_visionos_sdk_names_the_platform_to_install(self):
        import subprocess
        calls = []
        def run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 1 if cmd[:2] == ['xcrun', '--sdk'] else 0, '/Applications/Xcode.app/Contents/Developer\n', '')
        with patch.object(self.build.subprocess, 'run', run):
            with self.assertRaisesRegex(self.build.BuildError, 'visionOS SDK is not installed.*downloadPlatform visionOS'):
                self.build.check_ios_sdk({'DM_APP_PLATFORM': 'visionos'})
        self.assertEqual(calls[0][:3], ['xcrun', '--sdk', 'xros'])


if __name__ == '__main__':
    unittest.main()
