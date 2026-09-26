from pyrogram import Client, enums

import Videl.strings as strings
from Videl import custom_filter
from Videl.helper.chat_status import isUserCan
from Videl.modules.plugins.connection.connection import connection
from Videl.mongo.blocklists_mongo import setblocklistreason_db
from Videl.utils.decorators import *


@Client.on_message(custom_filter.command(commands="resetblocklistreason"))
@anonadmin_checker
async def resetblocklistreason(client, message):
    if await connection(message) is not None:
        chat_id = await connection(message)
    else:
        chat_id = message.chat.id

    if (
        not str(chat_id).startswith("-100")
        and message.chat.type == enums.ChatType.PRIVATE
    ):
        return await message.reply(strings.is_pvt)

    if not await isUserCan(message, chat_id=chat_id, privileges="can_change_info"):
        return

    await setblocklistreason_db(chat_id, None)
    await message.reply("The default blocklist reason has been reset.")
