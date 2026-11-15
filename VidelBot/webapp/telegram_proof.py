"""One-use claims for already verified Telegram Mini App login proofs.

Never call this before Telegram's signature, date and identity have been checked.
The stored identifier is a digest of the signed hash, never the launch data.
"""
from datetime import datetime, timedelta, timezone
import hashlib
from urllib.parse import parse_qsl

from aiohttp import web
from pymongo.errors import DuplicateKeyError
from pymongo.write_concern import WriteConcern

from webapp import db_models


async def consume_verified_init_data(init_data: str, valid_for_seconds: int) -> None:
    # Verification has already ruled out duplicate fields and malformed hashes.
    fields = dict(parse_qsl(init_data, strict_parsing=True, keep_blank_values=True, max_num_fields=64))
    digest = hashlib.sha256(('miniapp:' + fields['hash']).encode('ascii')).hexdigest()
    try:
        database = db_models.col('saas_telegram_login_proofs').database
        proofs = database.get_collection('saas_telegram_login_proofs', write_concern=WriteConcern(w='majority'))
        # The built-in _id index supplies the atomic uniqueness constraint, even
        # across workers. Fail closed if the TTL index cannot be established.
        await proofs.create_index('expires_at', expireAfterSeconds=0)
        await proofs.insert_one({'_id': digest, 'expires_at': datetime.now(timezone.utc) + timedelta(seconds=valid_for_seconds + 60)})
    except DuplicateKeyError:
        raise web.HTTPUnauthorized(text='{"error":"Telegram launch already used. Reopen Studio from the bot."}', content_type='application/json') from None
    except Exception:
        # An ambiguous insert remains consumed; a new launch can be used once
        # storage recovers. Do not log database exceptions or the proof.
        raise web.HTTPServiceUnavailable(text='{"error":"Telegram sign-in is temporarily unavailable. Reopen Studio or use a one-time link."}', content_type='application/json') from None
