from .storage import dispatch_upload


async def upload_worker(new_file, message, msg):
    return await dispatch_upload(new_file, message, msg)
