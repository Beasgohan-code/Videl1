"""Videl core: unified menus, global users/bans and extra utility tools."""

# ── Dynamic admins support ──────────────────────────────────────────────
# `filters.user(ADMINS)` copies the list when a plugin is imported, so admins
# added later with /add_admin would never pass those filters. We remember every
# filter instance that was built from *the* `config.ADMINS` list object (identity
# check – owner-only filters built from OWNERS are never touched) so core.admins
# can add / remove users on all of them at runtime.
import config as _config
from pyrogram import filters as _filters

ADMIN_FILTERS: list = []

if not getattr(_filters.user, "_videl_tracked", False):
    _orig_user_init = _filters.user.__init__

    def _tracked_user_init(self, users=None):
        _orig_user_init(self, users)
        if users is _config.ADMINS:
            ADMIN_FILTERS.append(self)

    _filters.user.__init__ = _tracked_user_init
    _filters.user._videl_tracked = True
