# CI media tools and Ubuntu package check

The Python verification job and the Ubuntu native-package check run independently
on GitHub's Ubuntu 24.04 image. Neither is `continue-on-error`, so a package
failure remains a visible failing check, but it no longer prevents pytest and
JSDOM from running. The app's Dockerfile and runtime dependencies are unchanged.

Python tests use FFmpeg and ffprobe from the **test-only** PyPI wheel
`ffmpeg-binaries==1.1.0` for manylinux x86-64, pinned by SHA-256 in
`scripts/ci/media-requirements.txt`. It is installed without dependencies into
`RUNNER_TEMP`, not the app's Python environment, and its executables are checked
before pytest. The wheel embeds John Van Sickle's FFmpeg 6.0 static build
(GPLv3) and a wrapper with an MIT license; the wheel contains the notices.
This introduces a third-party binary supply-chain dependency for CI only. Hash
pinning detects unexpected file changes, not vulnerabilities or compromised
upstream builds. This is not a production package recommendation; do not add the
wheel to the application image. Static FFmpeg 6 may behave differently from the
Ubuntu/runtime version. The separate job checks native tool availability, not runtime integration or codec parity.

The **Ubuntu native media & archive tools** job checks `ffmpeg`, `ffprobe`,
`mediainfo`, `mkvmerge`, `7z` and `git`. If absent,
`scripts/ci/install-native-tools.sh` installs the existing Ubuntu packages from
the image's Ubuntu source file only; unrelated third-party apt feeds are not
updated. If all are present, it does not contact apt. The image's Ubuntu mirror
configuration is tried first. If its update fails or times out, the check retries
with a temporary source file for the official Ubuntu 24.04 archive, verified
with the existing Ubuntu archive keyring. Each update attempt has a two-minute
process timeout and the install has a four-minute timeout; apt network requests
have a 30-second timeout and one retry. Install uses only whichever source passed
update. The temporary source is removed afterwards and does not change system
apt configuration. Phase-specific GitHub annotations report exit codes without
copying repository URLs or apt output into annotations. Source errors on both
attempts, missing tools or unusable FFmpeg fail the native job. This fallback is
an attempted recovery, not proof that the original failure was caused by a mirror.

Offline regression tests use temporary fake commands and never invoke real apt
or sudo: `python -m unittest VidelBot.tests.test_ci_native_tools -v` from the
repository root. Mock tests do not prove GitHub's runner or Ubuntu mirrors work.
Previous Python jobs timed out during native installation before pytest; the
bounded attempt subsequently failed with exit 100 in that step. Detailed logs
were inaccessible in the sandbox, so the exact cause remains unconfirmed.
