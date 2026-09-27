

from .lk21_patch import *   # ⭐ MUST BE FIRST LINE ⭐


from .. import LOGGER
from . import (direct_link_generator, display_progress, encoding, helper,
               settings, tasks)

LOGGER.info('Imported Utils!')
