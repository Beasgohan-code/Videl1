"""Standalone Music worker for clones.

A separate worker program for clones whose profile is Music — built
the same way as the File Store worker and the Group Management worker,
as its own codebase. It does not modify either of them.

Stage 1 provides the queue, settings, commands and rich UI. Actual
voice-chat streaming needs a verified PyTgCalls/WzGram-compatible
playback stack and a per-clone assistant session; until that lands,
playback commands honestly report that streaming is not set up instead
of pretending to play.
"""
