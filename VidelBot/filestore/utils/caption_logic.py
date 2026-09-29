import re
import aiohttp
from filestore.fs_config import LOGGER
log = LOGGER(__name__)
LANGUAGES = {
    "hindi": ["hindi", "hin"],
    "english": ["english", "eng"],
    "bengali": ["bengali", "ben"],
    "tamil": ["tamil", "tam"],
    "telugu": ["telugu", "tel"],
    "malayalam": ["malayalam", "mal"],
    "kannada": ["kannada", "kan"],
    "marathi": ["marathi", "mar"],
    "punjabi": ["punjabi", "pun"],
    "gujarati": ["gujarati", "guj"],
    "bhojpuri": ["bhojpuri"],
    "urdu": ["urdu", "urd"],
    "korean": ["korean", "kor"],
    "japanese": ["japanese", "jap"],
    "chinese": ["chinese", "chi"],
    "arabic": ["arabic", "ara"],
    "french": ["french", "fre"],
    "german": ["german", "ger"],
    "russian": ["russian", "rus"],
    "nepali": ["nepali", "nep"],
    "dual": ["dual"],
    "multi": ["multi", "multi audio", "multi-audio"],
    "dubbed": ["dubbed"],
    "subbed": ["subbed"]
}
FORMAT_KEYWORDS = [
    "hevc", "x265", "x264", "web-dl", "webdl", "hdrip", "hdtc", "hdtv", 
    "dvdscr", "brrip", "bluray", "blu-ray", "camrip", "webrip", "tvrip", 
    "dvdrip", "remux", "rip", "hdr", "uhd", "sd", "hd", "fullhd", 
    "hdr10", "dolby", "atmos", "dv", "dvdr"
]
def get_size(size):
    units = ["Bytes", "KB", "MB", "GB", "TB", "PB", "EB"]
    size = float(size)
    i = 0
    while size >= 1024.0 and i < len(units) - 1:
        i += 1
        size /= 1024.0
    return "%.2f %s" % (size, units[i])

def get_file_details(message) -> dict:
    if message.media:
        file_name, file_caption, file_size, duration, quality, file_language = "", "", "", "", "", ""
        for file_type in ("video", "audio", "document", "voice"):
            obj = getattr(message, file_type, None)
            if obj:
                file_name = re.sub(r"@\w+\s*", "", getattr(obj, "file_name", "")).replace("_", " ").replace(".", " ")
                file_caption = message.caption or ""
                file_size = get_size(obj.file_size) if obj.file_size else "Unknown Size"
                if file_type in ("audio", "video", "voice"):
                    if getattr(obj, "duration", None):
                        hours = int(obj.duration // 3600)
                        minutes = int((obj.duration % 3600) // 60)
                        seconds = int(obj.duration % 60)
                        if hours > 0:
                            duration = f"{hours} Hr {minutes} Min {seconds} Sec"
                        else:
                            duration = f"{minutes} Min {seconds} Sec"
                    else:
                        duration = ""
                quality_match = re.search(r'(\d{3,4}p)', file_name)
                quality = quality_match.group(1) if quality_match else "Unknown Quality"
                file_languages = []
                for lang_name, aliases in LANGUAGES.items():
                    for alias in aliases:
                        if alias in file_caption.lower():
                            file_languages.append(lang_name.capitalize())
                            break
                file_language = ", ".join(file_languages) if file_languages else "Unknown Audio"
                
                break
        return {
            "file_name": file_name,
            "file_caption": file_caption,
            "file_size": file_size,
            "duration": duration,
            "quality": quality,
            "language": file_language
        }
    return {}

def format_caption(caption: str, file_details: dict) -> str:
    caption = caption.replace("{file_name}", file_details.get("file_name", ""))
    caption = caption.replace("{previouscaption}", file_details.get("previouscaption", ""))
    caption = caption.replace("{filename}", file_details.get("filename", ""))
    caption = caption.replace("{username}", file_details.get("username", ""))
    caption = caption.replace("{file_caption}", file_details.get("file_caption", ""))
    caption = caption.replace("{size}", file_details.get("file_size", ""))
    caption = caption.replace("{duration}", file_details.get("duration", ""))
    caption = caption.replace("{quality}", file_details.get("quality", ""))
    caption = caption.replace("{language}", file_details.get("language", ""))
    caption = caption.replace("{name}", file_details.get("name", ""))
    caption = caption.replace("{season}", file_details.get("season", ""))
    caption = caption.replace("{format}", file_details.get("format", ""))
    return caption


