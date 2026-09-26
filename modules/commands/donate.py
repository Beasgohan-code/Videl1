from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from Videl.custom_filter import register


@register(pattern="donate")
async def handle_donate(client, message):
    text = "🌟 Thank you for considering a donation! 🌟\n\n"
    text += "Your support helps us continue providing great services.\n\n"
    text += "To donate, please click on the button below.\n"
    text += "We appreciate your generosity! ❤️"
    button = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Donate", url="https://t.me/VidelBot/donation")]]
    )
    await message.reply_text(text, reply_markup=button)
