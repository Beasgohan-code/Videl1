from Videl.custom_filter import register
from Videl.utils.decorators import *


@register(pattern="test")
@rate_limit(RATE_LIMIT_GENERAL)
async def test(client, event):
    await event.reply("test")
