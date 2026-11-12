"""Offline tests for CI's native-tool setup; never invokes real apt or sudo."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/ci/install-native-tools.sh'
TOOLS = ('ffmpeg', 'ffprobe', 'mediainfo', 'mkvmerge', '7z', 'git')


class NativeToolsSetupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.source = self.root / 'ubuntu.sources'
        self.source.write_text('synthetic sources; mock sudo never reads it')
        self.log = self.root / 'calls'
        self.timeouts = self.root / 'timeouts'
        self.fallback = self.root / 'fallback-copy'
        self.marker = self.root / 'update-failed'
        self.env = {**os.environ, 'PATH': str(self.bin), 'MOCK_LOG': str(self.log),
                    'MOCK_BIN': str(self.bin), 'MOCK_TIMEOUT_LOG': str(self.timeouts),
                    'MOCK_FALLBACK_COPY': str(self.fallback), 'MOCK_MARKER': str(self.marker)}
        self.exe('timeout', '#!/bin/bash\nprintf \"%s\\n\" \"$*\" >> \"$MOCK_TIMEOUT_LOG\"\nshift 3\nexec \"$@\"\n')
        for name in ('mktemp', 'rm', 'rmdir'):
            self.exe(name, f'#!/bin/bash\nexec /usr/bin/{name} "$@"\n')
        self.exe('sudo', '''#!/bin/bash
printf '%s\\n' "$*" >> "$MOCK_LOG"
if [[ " $* " == *" update "* ]]; then
  if [[ "${MOCK_FAIL_UPDATE:-}" == always ]]; then exit 23; fi
  if [[ "${MOCK_FAIL_UPDATE:-}" == once && ! -e "$MOCK_MARKER" ]]; then
    : > "$MOCK_MARKER"; exit 23
  fi
  for arg in "$@"; do
    if [[ "$arg" == Dir::Etc::sourcelist=* && "$arg" == *"/ubuntu.sources"* && -e "$MOCK_MARKER" ]]; then
      while IFS= read -r line; do printf '%s\\n' "$line" >> "$MOCK_FALLBACK_COPY"; done < "${arg#*=}"
    fi
  done
fi
if [[ " $* " == *" install "* ]]; then
  if [[ "${MOCK_FAIL_INSTALL:-}" == 1 ]]; then exit 24; fi
  for name in ffmpeg ffprobe mediainfo mkvmerge 7z git; do
    printf '#!/bin/bash\\nexit 0\\n' > "$MOCK_BIN/$name"
    /bin/chmod +x "$MOCK_BIN/$name"
  done
fi
''')

    def exe(self, name, content='#!/bin/bash\nexit 0\n'):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run_setup(self):
        return subprocess.run(['/bin/bash', str(SCRIPT), str(self.source)], env=self.env,
                              capture_output=True, text=True, timeout=5)

    def test_installs_only_using_ubuntu_source_with_bounded_update(self):
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text().splitlines()
        self.assertEqual(len(calls), 2)
        timeouts = self.timeouts.read_text().splitlines()
        self.assertEqual(len(timeouts), 2)
        self.assertTrue(timeouts[0].startswith('--signal=TERM --kill-after=10s 120s sudo '))
        self.assertTrue(timeouts[1].startswith('--signal=TERM --kill-after=10s 240s sudo '))
        for call in calls:
            self.assertIn(f'Dir::Etc::sourcelist={self.source}', call)
            self.assertIn('Dir::Etc::sourceparts=-', call)
            self.assertIn('Acquire::http::Timeout=30', call)
        self.assertIn('APT::Update::Error-Mode=any update', calls[0])
        self.assertIn('ffmpeg mediainfo mkvtoolnix p7zip-full git', calls[1])

    def test_update_failure_stops_before_install(self):
        self.env['MOCK_FAIL_UPDATE'] = 'always'
        result = self.run_setup()
        self.assertEqual(result.returncode, 23)
        self.assertEqual(len(self.log.read_text().splitlines()), 2)
        self.assertIn('Official archive apt update exited 23', result.stdout)


    def test_fallback_uses_signed_official_archive_for_install(self):
        self.env['MOCK_FAIL_UPDATE'] = 'once'
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text().splitlines()
        self.assertEqual(len(calls), 3)
        source_arg = next(part for part in calls[1].split() if part.startswith('Dir::Etc::sourcelist='))
        self.assertIn(source_arg, calls[2])
        self.assertNotIn(str(self.source), calls[2])
        contents = self.fallback.read_text()
        self.assertIn('URIs: https://archive.ubuntu.com/ubuntu', contents)
        self.assertIn('Suites: noble noble-updates noble-backports noble-security', contents)
        self.assertIn('Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg', contents)
        self.assertFalse(Path(source_arg.split('=', 1)[1]).exists())

    def test_install_failure_is_visible_and_never_retried(self):
        self.env['MOCK_FAIL_INSTALL'] = '1'
        result = self.run_setup()
        self.assertEqual(result.returncode, 24)
        self.assertIn('apt install exited 24', result.stdout)
        self.assertEqual(len(self.log.read_text().splitlines()), 2)

    def test_missing_source_fails_before_running_apt(self):
        self.source.unlink()
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('missing or unreadable', result.stderr)
        self.assertFalse(self.log.exists())

    def test_preinstalled_tools_do_not_touch_network(self):
        for tool in TOOLS:
            self.exe(tool)
        self.source.unlink()
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())

    def test_broken_preinstalled_ffmpeg_fails(self):
        for tool in TOOLS:
            self.exe(tool)
        self.exe('ffmpeg', '#!/bin/bash\nexit 9\n')
        self.assertEqual(self.run_setup().returncode, 9)
        self.assertFalse(self.log.exists())

    def test_unavailable_tools_after_install_fail(self):
        self.exe('sudo', '#!/bin/bash\nexit 0\n')
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Required native tool unavailable', result.stderr)


if __name__ == '__main__':
    unittest.main()
