"""Standalone Group Management worker for clones.

This package is a *separate worker program* for clones whose profile is
Group Management — the same way ``filestore/worker_bot`` is the worker
program for File Store clones. It intentionally does not import or copy
the main bot's ``plugins/group_mod`` plugin, and it does not touch the
File Store worker. Its command names, settings model and moderation
logic are its own.

Runtime wiring (which process starts these bots) is deliberately not
decided yet; nothing in ``run.py`` imports this package today.
"""
