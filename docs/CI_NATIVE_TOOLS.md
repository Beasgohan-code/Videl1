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
configuration still applies. The update and install commands each have a
four-minute process timeout; apt network requests have a 30-second timeout and
one retry. Source errors, missing tools or unusable FFmpeg fail the native job.

Offline regression tests use temporary fake commands and never invoke real apt
or sudo: `python -m unittest VidelBot.tests.test_ci_native_tools -v` from the
repository root. Mock tests do not prove GitHub's runner or Ubuntu mirrors work.
Previous Python jobs timed out during native installation before pytest; the
bounded attempt subsequently failed with exit 100 in that step. Detailed logs
were inaccessible in the sandbox, so the exact cause remains unconfirmed.
