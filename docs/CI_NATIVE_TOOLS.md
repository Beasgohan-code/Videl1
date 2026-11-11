# CI native media tools

The Python verification job runs on GitHub's Ubuntu 24.04 image. It checks for
`ffmpeg`, `ffprobe`, `mediainfo`, `mkvmerge`, `7z` and `git`. If any is absent,
`scripts/ci/install-native-tools.sh` installs the existing Ubuntu packages from
that image's Ubuntu source file only; unrelated third-party apt feeds are not
updated. If all are already present, it does not contact apt. The image's Ubuntu
mirror configuration still applies.

The update and install commands each have a four-minute process timeout; apt
network requests have a 30-second timeout and one retry. An apt source error,
missing source, failed install, missing tool or unusable ffmpeg/ffprobe fails
the job instead of silently running fewer media tests. The job's setup step is
limited to ten minutes and the whole job to thirty. This does not change the
runtime Docker image or replace production media integration testing.

Offline regression tests use temporary fake commands and never invoke real apt
or sudo: `python -m unittest VidelBot.tests.test_ci_native_tools -v` from the
repository root. A successful mock test is not proof that GitHub's runner or
Ubuntu mirrors are healthy; check the new Actions run for that evidence. The
previous Python runs were cancelled during native installation before pytest;
their exact network/repository cause was not available in the sandbox logs.
