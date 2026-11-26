
#

import os

if os.environ.get("MUSIC_WORKER_STANDALONE", "").strip() != "1":
    raise SystemExit(
        "Refusing to start: set MUSIC_WORKER_STANDALONE=1 and use the Videl Music worker entry point."
    )

from AoiMusic.core.bot import AyuBot
from AoiMusic.core.dir import dirr
from AoiMusic.core.git import git
from AoiMusic.core.userbot import Userbot
from AoiMusic.misc import dbb, heroku, sudo

from .logging import LOGGER

# Directories
dirr()

# Check Git Updates
git()

# Initialize Memory DB
dbb()

# Heroku APP
heroku()

# Load Sudo Users from DB
sudo()

# Bot Client
app = AyuBot()

# Assistant Client
userbot = Userbot()

from .platforms import PlaTForms

Platform = PlaTForms()
HELPABLE = {}

# The project's own release number. Not what the startup banner's py-tgcalls line
# reports — that line reports the installed py-tgcalls, read from the package
# metadata, because the label says py-tgcalls and it should say py-tgcalls.
__version__ = "2.3.3"
