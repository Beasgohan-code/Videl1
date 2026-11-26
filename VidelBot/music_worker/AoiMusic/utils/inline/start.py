#
# Start & Help inline keyboards — XENOBOT2-style normal buttons with Musiciconis premium emoji icons.
#

from typing import Union

from config import GITHUB_REPO, SUPPORT_CHANNEL, SUPPORT_GROUP
from AoiMusic import app
from AoiMusic.utils.rich_msg import (
    rich_button,
    rich_button_row,
    rich_url_button,
    rich_user_button,
)


def _support_rows(_):
    rows = []
    if SUPPORT_CHANNEL and SUPPORT_GROUP:
        rows.append(
            rich_button_row(
                rich_url_button(_["S_B_4"], f"{SUPPORT_CHANNEL}", style="success", emoji="MEGAPHONE"),
                rich_url_button(_["S_B_3"], f"{SUPPORT_GROUP}", style="success", emoji="CHAT_BUBBLE"),
            )
        )
    else:
        if SUPPORT_CHANNEL:
            rows.append(rich_button_row(rich_url_button(_["S_B_4"], f"{SUPPORT_CHANNEL}", style="success", emoji="MEGAPHONE")))
        if SUPPORT_GROUP:
            rows.append(rich_button_row(rich_url_button(_["S_B_3"], f"{SUPPORT_GROUP}", style="success", emoji="CHAT_BUBBLE")))
    return "".join(rows)


def start_pannel(_):
    rows = rich_button_row(
        rich_url_button(_["S_B_5"], f"https://t.me/{app.username}?startgroup=true", style="primary", emoji="BOT"),
        rich_button(_["S_B_2"], callback_data="settings_helper", style="primary", emoji="SETTINGS"),
    )
    return rows + _support_rows(_)


def private_panel(_, BOT_USERNAME, OWNER: Union[bool, int] = None):
    # Row 1: Add Bot
    rows = rich_button_row(
        rich_url_button(_["S_B_5"], f"https://t.me/{BOT_USERNAME}?startgroup=true", style="primary", emoji="BOT"),
    )
    # Row 2: Channel & Support
    rows += _support_rows(_)
    # Row 3: Language
    rows += rich_button_row(
        rich_button(_["ST_B_6"], callback_data="LG", style="", emoji="GLOBE_LANG"),
    )
    # Row 4: Dev & Repo
    dev_repo = []
    if OWNER:
        dev_repo.append(rich_user_button(_["S_B_7"], int(OWNER), style="", emoji="CROWN_OWNER"))
    if GITHUB_REPO:
        dev_repo.append(rich_url_button(_["S_B_6"], f"{GITHUB_REPO}", style="", emoji="LINK"))
    if dev_repo:
        rows += rich_button_row(*dev_repo)
    # Row 5: Commands
    rows += rich_button_row(
        rich_button(_["S_B_8"], callback_data="settings_back_helper", style="primary", emoji="GRID"),
    )
    return rows


def support_group_markup(_):
    return rich_button_row(
        rich_url_button(_["S_B_3"], SUPPORT_GROUP, style="success", emoji="CHAT_BUBBLE"),
    )


def help_back_markup(_):
    return rich_button_row(
        rich_button(_["BACK_BUTTON"], callback_data="settings_back_helper", style="primary", emoji="BACK_ARROW"),
        rich_button(_["CLOSE_BUTTON"], callback_data="close", style="danger", emoji="CLOSE"),
    )


def private_help_panel(_):
    return rich_button_row(
        rich_url_button(_["S_B_1"], f"https://t.me/{app.username}?start=help", style="primary", emoji="GRID"),
    )
