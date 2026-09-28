"""
🌐 Languages – the home screen, its buttons and the language picker in 8 languages.

  t(key, lang, **kw)   translated string (English fallback for missing keys / languages)
  get_lang(user)       stored choice → else the user's Telegram language (if supported) → else English
  set_lang(uid, code)  saved in videl_users.lang (+ in-memory cache)
  /lang · 🌐 button    picker

English output is exactly the original texts.START_TXT / home keyboard, so nothing changes for English users.
"""
import logging

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

log = logging.getLogger("videl.i18n")

LANGS = {
    "en": "🇬🇧 English", "hi": "🇮🇳 हिन्दी", "ml": "🇮🇳 മലയാളം", "ta": "🇮🇳 தமிழ்",
    "es": "🇪🇸 Español", "id": "🇮🇩 Indonesia", "ru": "🇷🇺 Русский", "ar": "🇸🇦 العربية",
}
DEFAULT = "en"
FEATURES = ("save", "encode", "rename", "clone", "tools", "premium")
FEATURE_EMOJI = {"save": "📥", "encode": "🎬", "rename": "✏️", "clone": "⚡", "tools": "🧰", "premium": "💎"}

S = {
    "welcome": {"en": "Welcome", "hi": "स्वागत है", "ml": "സ്വാഗതം", "ta": "வரவேற்கிறோம்", "es": "Bienvenido",
                "id": "Selamat datang", "ru": "Добро пожаловать", "ar": "مرحبًا"},
    "hello": {"en": "Hello", "hi": "नमस्ते", "ml": "ഹലോ", "ta": "வணக்கம்", "es": "Hola", "id": "Halo",
              "ru": "Привет", "ar": "أهلًا"},
    "iam": {"en": "I'm", "hi": "मैं हूँ", "ml": "ഞാൻ", "ta": "நான்", "es": "Soy", "id": "Saya", "ru": "Я —",
            "ar": "أنا"},
    "tagline": {
        "en": "your all-in-one Telegram workspace. Save, encode, rename and share files, all from one chat.",
        "hi": "आपका ऑल-इन-वन Telegram वर्कस्पेस। फ़ाइलें सेव, एन्कोड, रीनेम और शेयर करें – सब एक ही चैट से।",
        "ml": "നിങ്ങളുടെ ഓൾ-ഇൻ-വൺ Telegram വർക്ക്‌സ്‌പേസ്. ഫയലുകൾ സേവ്, എൻകോഡ്, റീനെയിം, ഷെയർ – എല്ലാം ഒരൊറ്റ ചാറ്റിൽ.",
        "ta": "உங்கள் ஆல்-இன்-ஒன் Telegram பணியிடம். கோப்புகளைச் சேமிக்க, என்கோட் செய்ய, பெயர் மாற்ற, பகிர – எல்லாம் ஒரே அரட்டையில்.",
        "es": "tu espacio de trabajo todo en uno en Telegram. Guarda, codifica, renombra y comparte archivos desde un solo chat.",
        "id": "ruang kerja Telegram serba bisa. Simpan, encode, ganti nama, dan bagikan file – semua dari satu chat.",
        "ru": "ваше универсальное рабочее место в Telegram. Сохраняйте, кодируйте, переименовывайте и делитесь файлами в одном чате.",
        "ar": "مساحة عملك الشاملة في Telegram. احفظ الملفات وحوّلها وأعد تسميتها وشاركها – كل ذلك من محادثة واحدة.",
    },
    "status": {"en": "Status", "hi": "स्थिति", "ml": "സ്റ്റാറ്റസ്", "ta": "நிலை", "es": "Estado", "id": "Status",
               "ru": "Статус", "ar": "الحالة"},
    "online": {"en": "Online", "hi": "ऑनलाइन", "ml": "ഓൺലൈൻ", "ta": "ஆன்லைன்", "es": "En línea", "id": "Online",
               "ru": "В сети", "ar": "متصل"},
    "uptime": {"en": "Uptime", "hi": "अपटाइम", "ml": "അപ്‌ടൈം", "ta": "இயக்க நேரம்", "es": "Activo", "id": "Aktif",
               "ru": "Аптайм", "ar": "مدة التشغيل"},
    "plan": {"en": "Your plan", "hi": "आपका प्लान", "ml": "നിങ്ങളുടെ പ്ലാൻ", "ta": "உங்கள் திட்டம்", "es": "Tu plan",
             "id": "Paket kamu", "ru": "Ваш тариф", "ar": "خطتك"},
    "can_do": {"en": "What I can do", "hi": "मैं क्या कर सकता हूँ", "ml": "എനിക്ക് എന്തൊക്കെ ചെയ്യാം",
               "ta": "நான் என்ன செய்ய முடியும்", "es": "Lo que puedo hacer", "id": "Yang bisa saya lakukan",
               "ru": "Что я умею", "ar": "ما يمكنني فعله"},
    "choose": {"en": "Choose an option below to get started", "hi": "शुरू करने के लिए नीचे एक विकल्प चुनें",
               "ml": "തുടങ്ങാൻ താഴെ ഒരു ഓപ്ഷൻ തിരഞ്ഞെടുക്കൂ", "ta": "தொடங்க கீழே ஒன்றைத் தேர்ந்தெடுக்கவும்",
               "es": "Elige una opción para empezar", "id": "Pilih opsi di bawah untuk mulai",
               "ru": "Выберите пункт ниже, чтобы начать", "ar": "اختر أحد الخيارات أدناه للبدء"},
    # features: title | description
    "f.save": {"en": "Save|posts from restricted channels & groups",
               "hi": "सेव|प्रतिबंधित चैनल और ग्रुप की पोस्ट", "ml": "സേവ്|നിയന്ത്രിത ചാനലുകളിലെയും ഗ്രൂപ്പുകളിലെയും പോസ്റ്റുകൾ",
               "ta": "சேமி|கட்டுப்படுத்தப்பட்ட சேனல்கள் & குழுக்களின் பதிவுகள்",
               "es": "Guardar|publicaciones de canales y grupos restringidos",
               "id": "Simpan|postingan dari channel & grup terbatas", "ru": "Сохранение|посты из закрытых каналов и групп",
               "ar": "الحفظ|منشورات القنوات والمجموعات المقيّدة"},
    "f.encode": {"en": "Encode|shrink videos with x264 / x265",
                 "hi": "एन्कोड|H.264 / H.265 / AV1 से वीडियो छोटा करें", "ml": "എൻകോഡ്|H.264 / H.265 / AV1 ഉപയോഗിച്ച് വീഡിയോ ചെറുതാക്കൂ",
                 "ta": "என்கோட்|H.264 / H.265 / AV1 மூலம் வீடியோவைச் சுருக்கு",
                 "es": "Codificar|reduce videos con H.264 / H.265 / AV1", "id": "Encode|perkecil video dengan H.264 / H.265 / AV1",
                 "ru": "Кодирование|сжатие видео H.264 / H.265 / AV1", "ar": "الترميز|تصغير الفيديو بـ H.264 / H.265 / AV1"},
    "f.rename": {"en": "Auto-Rename|clean names, metadata & thumbnails",
                 "hi": "ऑटो-रीनेम|साफ़ नाम, मेटाडेटा और थंबनेल", "ml": "ഓട്ടോ-റീനെയിം|വൃത്തിയുള്ള പേരുകൾ, മെറ്റാഡാറ്റ, തംബ്‌നെയിലുകൾ",
                 "ta": "தானியங்கு பெயர்மாற்றம்|சுத்தமான பெயர்கள், மெட்டாடேட்டா & சிறுபடங்கள்",
                 "es": "Auto-renombrar|nombres limpios, metadatos y miniaturas",
                 "id": "Ganti nama otomatis|nama rapi, metadata & thumbnail",
                 "ru": "Автопереименование|чистые имена, метаданные и обложки",
                 "ar": "إعادة التسمية|أسماء نظيفة وبيانات وصفية وصور مصغّرة"},
    "f.clone": {"en": "Clone|launch your own FileStore bot", "hi": "क्लोन|अपना FileStore बॉट बनाएं",
                "ml": "ക്ലോൺ|സ്വന്തം FileStore ബോട്ട് തുടങ്ങൂ", "ta": "குளோன்|உங்கள் சொந்த FileStore பாட்டைத் தொடங்கு",
                "es": "Clonar|lanza tu propio bot FileStore", "id": "Klon|buat bot FileStore milikmu",
                "ru": "Клон|запустите собственного FileStore-бота", "ar": "النسخ|أطلق بوت FileStore الخاص بك"},
    "f.tools": {"en": "Tools|MediaInfo, uploads, QR codes, short links",
                "hi": "टूल्स|MediaInfo, अपलोड, QR कोड, शॉर्ट लिंक", "ml": "ടൂളുകൾ|MediaInfo, അപ്‌ലോഡ്, QR കോഡ്, ഷോർട്ട് ലിങ്ക്",
                "ta": "கருவிகள்|MediaInfo, பதிவேற்றம், QR குறியீடு, குறு இணைப்புகள்",
                "es": "Herramientas|MediaInfo, subidas, códigos QR, enlaces cortos",
                "id": "Alat|MediaInfo, unggahan, kode QR, tautan pendek",
                "ru": "Инструменты|MediaInfo, загрузки, QR-коды, короткие ссылки",
                "ar": "أدوات|MediaInfo ورفع الملفات ورموز QR والروابط المختصرة"},
    "f.premium": {"en": "Premium|Telegram Stars, gifts & referrals", "hi": "प्रीमियम|Telegram Stars, गिफ्ट और रेफ़रल",
                  "ml": "പ്രീമിയം|Telegram Stars, സമ്മാനങ്ങൾ, റെഫറലുകൾ", "ta": "பிரீமியம்|Telegram Stars, பரிசுகள் & பரிந்துரைகள்",
                  "es": "Premium|Telegram Stars, regalos y referidos", "id": "Premium|Telegram Stars, hadiah & referral",
                  "ru": "Премиум|Telegram Stars, подарки и рефералы", "ar": "بريميوم|نجوم Telegram والهدايا والإحالات"},
    # buttons
    "b.clone": {"en": "⚡ Clone Bot", "hi": "⚡ क्लोन बॉट", "ml": "⚡ ക്ലോൺ ബോട്ട്", "ta": "⚡ குளோன் பாட்", "es": "⚡ Clonar bot",
                "id": "⚡ Klon Bot", "ru": "⚡ Клон-бот", "ar": "⚡ نسخ البوت"},
    "b.encoder": {"en": "🎬 Encoder", "hi": "🎬 एन्कोडर", "ml": "🎬 എൻകോഡർ", "ta": "🎬 என்கோடர்", "es": "🎬 Codificador",
                  "id": "🎬 Encoder", "ru": "🎬 Кодировщик", "ar": "🎬 المرمّز"},
    "b.rename": {"en": "✏️ Auto-Rename", "hi": "✏️ ऑटो-रीनेम", "ml": "✏️ ഓട്ടോ-റീനെയിം", "ta": "✏️ பெயர்மாற்றம்",
                 "es": "✏️ Renombrar", "id": "✏️ Ganti Nama", "ru": "✏️ Переименование", "ar": "✏️ إعادة التسمية"},
    "b.tools": {"en": "🧰 Tools", "hi": "🧰 टूल्स", "ml": "🧰 ടൂളുകൾ", "ta": "🧰 கருவிகள்", "es": "🧰 Herramientas",
                "id": "🧰 Alat", "ru": "🧰 Инструменты", "ar": "🧰 الأدوات"},
    "b.settings": {"en": "⚙️ Settings", "hi": "⚙️ सेटिंग्स", "ml": "⚙️ ക്രമീകരണങ്ങൾ", "ta": "⚙️ அமைப்புகள்",
                   "es": "⚙️ Ajustes", "id": "⚙️ Pengaturan", "ru": "⚙️ Настройки", "ar": "⚙️ الإعدادات"},
    "b.help": {"en": "🆘 Help & Guide", "hi": "🆘 मदद और गाइड", "ml": "🆘 സഹായം & ഗൈഡ്", "ta": "🆘 உதவி & வழிகாட்டி",
               "es": "🆘 Ayuda y guía", "id": "🆘 Bantuan & Panduan", "ru": "🆘 Помощь", "ar": "🆘 المساعدة والدليل"},
    "b.premium": {"en": "💎 Premium", "hi": "💎 प्रीमियम", "ml": "💎 പ്രീമിയം", "ta": "💎 பிரீமியம்", "es": "💎 Premium",
                  "id": "💎 Premium", "ru": "💎 Премиум", "ar": "💎 بريميوم"},
    "b.refer": {"en": "🤝 Refer & Earn", "hi": "🤝 रेफ़र करें और कमाएं", "ml": "🤝 റെഫർ & നേടൂ", "ta": "🤝 பரிந்துரை & சம்பாதி",
                "es": "🤝 Invita y gana", "id": "🤝 Ajak & Dapatkan", "ru": "🤝 Пригласи друга", "ar": "🤝 ادعُ واربح"},
    "b.about": {"en": "ℹ️ About", "hi": "ℹ️ परिचय", "ml": "ℹ️ വിവരം", "ta": "ℹ️ பற்றி", "es": "ℹ️ Acerca de",
                "id": "ℹ️ Tentang", "ru": "ℹ️ О боте", "ar": "ℹ️ حول"},
    "b.support": {"en": "💬 Support", "hi": "💬 सहायता", "ml": "💬 സപ്പോർട്ട്", "ta": "💬 ஆதரவு", "es": "💬 Soporte",
                  "id": "💬 Dukungan", "ru": "💬 Поддержка", "ar": "💬 الدعم"},
    "b.channels": {"en": "📢 Channels", "hi": "📢 चैनल", "ml": "📢 ചാനലുകൾ", "ta": "📢 சேனல்கள்", "es": "📢 Canales",
                   "id": "📢 Channel", "ru": "📢 Каналы", "ar": "📢 القنوات"},
    "b.language": {"en": "🌐 Language", "hi": "🌐 भाषा", "ml": "🌐 ഭാഷ", "ta": "🌐 மொழி", "es": "🌐 Idioma",
                   "id": "🌐 Bahasa", "ru": "🌐 Язык", "ar": "🌐 اللغة"},
    "b.back": {"en": "⬅️ Back to Home", "hi": "⬅️ होम पर वापस", "ml": "⬅️ ഹോമിലേക്ക്", "ta": "⬅️ முகப்புக்கு",
               "es": "⬅️ Volver al inicio", "id": "⬅️ Kembali", "ru": "⬅️ На главную", "ar": "⬅️ العودة للرئيسية"},
    # picker
    "lang.title": {"en": "Choose your language", "hi": "अपनी भाषा चुनें", "ml": "നിങ്ങളുടെ ഭാഷ തിരഞ്ഞെടുക്കൂ",
                   "ta": "உங்கள் மொழியைத் தேர்ந்தெடுக்கவும்", "es": "Elige tu idioma", "id": "Pilih bahasamu",
                   "ru": "Выберите язык", "ar": "اختر لغتك"},
    "lang.note": {"en": "The home screen and menus follow your choice. Commands stay in English.",
                  "hi": "होम स्क्रीन और मेन्यू आपकी भाषा में दिखेंगे। कमांड अंग्रेज़ी में रहेंगे।",
                  "ml": "ഹോം സ്ക്രീനും മെനുകളും നിങ്ങളുടെ ഭാഷയിൽ. കമാൻഡുകൾ ഇംഗ്ലീഷിൽ തന്നെ.",
                  "ta": "முகப்புத் திரையும் மெனுக்களும் உங்கள் மொழியில். கட்டளைகள் ஆங்கிலத்திலேயே இருக்கும்.",
                  "es": "La pantalla de inicio y los menús usarán tu idioma. Los comandos siguen en inglés.",
                  "id": "Layar utama dan menu mengikuti pilihanmu. Perintah tetap dalam bahasa Inggris.",
                  "ru": "Главный экран и меню будут на выбранном языке. Команды остаются на английском.",
                  "ar": "ستظهر الشاشة الرئيسية والقوائم بلغتك. تبقى الأوامر بالإنجليزية."},
    "lang.set": {"en": "Language: English ✅", "hi": "भाषा: हिन्दी ✅", "ml": "ഭാഷ: മലയാളം ✅", "ta": "மொழி: தமிழ் ✅",
                 "es": "Idioma: Español ✅", "id": "Bahasa: Indonesia ✅", "ru": "Язык: русский ✅",
                 "ar": "اللغة: العربية ✅"},
}

_cache: dict = {}


def fallback() -> str:
    """DEFAULT_LANG env (for users whose Telegram language isn't one of ours)."""
    try:
        import config
        return config.DEFAULT_LANG if config.DEFAULT_LANG in LANGS else DEFAULT
    except Exception:
        return DEFAULT


def norm(code: str | None) -> str:
    code = (code or "").lower().replace("_", "-").split("-")[0]
    return code if code in LANGS else fallback()


def t(key: str, lang: str = DEFAULT, **kw) -> str:
    entry = S.get(key) or {}
    text = entry.get(lang) or entry.get(DEFAULT) or key
    return text.format(**kw) if kw else text


async def get_lang(user) -> str:
    """user object (or id). Stored choice → Telegram language_code → English."""
    uid = getattr(user, "id", user)
    if uid in _cache:
        return _cache[uid]
    lang = None
    try:
        from core.db import vdb
        doc = await vdb.users.find_one({"id": uid}, {"lang": 1})
        lang = (doc or {}).get("lang")
    except Exception as e:
        log.debug(f"get_lang: {e}")
    lang = lang if lang in LANGS else norm(getattr(user, "language_code", None))
    if len(_cache) > 50000:
        _cache.clear()
    _cache[uid] = lang
    return lang


async def set_lang(uid: int, lang: str) -> str:
    lang = lang if lang in LANGS else DEFAULT
    _cache[uid] = lang
    try:
        from core.db import vdb
        await vdb.users.update_one({"id": uid}, {"$set": {"lang": lang}}, upsert=True)
    except Exception as e:
        log.warning(f"set_lang: {e}")
    return lang


def start_template(lang: str) -> str:
    """START_TXT in `lang` (same layout and placeholders: mention username first_name uptime plan)."""
    from core import texts
    if lang == DEFAULT or lang not in LANGS:
        return texts.START_TXT
    from core.style import hdr, quote, rows, sc
    feats = []
    for f in FEATURES:
        title, desc = t(f"f.{f}", lang).split("|", 1)
        feats.append(f"◈ {FEATURE_EMOJI[f]} <b>{sc(title)}</b> — {sc(desc)}")
    rtl = lang == "ar"
    body = (hdr("👋", t("welcome", lang)) + "\n\n"
            + quote(sc(t("hello", lang)) + " {mention}!\n" + sc(t("iam", lang))
                    + " <b><a href=https://t.me/{username}>{first_name}</a></b> — " + sc(t("tagline", lang)))
            + "\n" + quote(rows([("🟢 " + t("status", lang), t("online", lang)), ("⏱ " + t("uptime", lang), "{uptime}"),
                                 ("👤 " + t("plan", lang), "{plan}")]))
            + "\n" + quote("<b>✦ " + sc(t("can_do", lang)) + " ✦</b>\n" + "\n".join(feats), expandable=True)
            + "\n<b>" + sc(t("choose", lang)) + " 👇</b>")
    return ("\u200f" + body) if rtl else body


def picker_kb(current: str, back: str = "start_btn") -> InlineKeyboardMarkup:
    codes = list(LANGS)
    rows_ = [[Btn(("✅ " if c == current else "") + LANGS[c], callback_data=f"setlang:{c}") for c in codes[i:i + 2]]
             for i in range(0, len(codes), 2)]
    rows_.append([Btn(t("b.back", current), callback_data=back)])
    return InlineKeyboardMarkup(rows_)


def picker_text(lang: str) -> str:
    from core.style import hdr, hint
    return hdr("🌐", t("lang.title", lang)) + "\n\n" + hint(t("lang.note", lang))


@Client.on_message(filters.command(["lang", "language"]) & filters.private)
async def lang_cmd(client: Client, message: Message):
    lang = await get_lang(message.from_user)
    await message.reply_text(picker_text(lang), reply_markup=picker_kb(lang))


@Client.on_callback_query(filters.regex(r"^lang_menu$"))
async def lang_menu_cb(client: Client, query: CallbackQuery):
    lang = await get_lang(query.from_user)
    await query.answer()
    from core.ui import smart_edit
    await smart_edit(query.message, picker_text(lang), picker_kb(lang))


@Client.on_callback_query(filters.regex(r"^setlang:\w+$"))
async def setlang_cb(client: Client, query: CallbackQuery):
    lang = await set_lang(query.from_user.id, query.data.split(":", 1)[1])
    await query.answer(t("lang.set", lang))
    from core.menus import render_home
    await render_home(client, query)
