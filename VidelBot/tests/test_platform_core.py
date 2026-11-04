"""
Unit & Integration Tests for `VidelBot/platform_core` (Providers, Media Studio,
Settings Engine, and Scheduled Jobs).
"""
from __future__ import annotations

import os
import time
import zipfile
import pytest
from PIL import Image
from pypdf import PdfWriter

from tests.harness import load_all, reset_db, run

load_all()

from platform_core import media_studio, models_db, providers  # noqa: E402


def test_ssrf_guard_blocks_loopback_and_link_local():
    async def go():
        with pytest.raises(providers.ProviderError):
            await providers.check_public_url("http://127.0.0.1:8080/secret")
        with pytest.raises(providers.ProviderError):
            await providers.check_public_url("http://169.254.169.254/latest/meta-data")
        with pytest.raises(providers.ProviderError):
            await providers.check_public_url("file:///etc/passwd")

    run(go())


def test_media_studio_image_pdf_zip_and_checksum_tools(tmp_path):
    ws = str(tmp_path)
    img_path = os.path.join(ws, "sample.png")
    Image.new("RGBA", (120, 80), (40, 110, 220, 255)).save(img_path)

    webp_path = media_studio.image_convert_sync(img_path, ws, target_fmt="webp", quality=80)
    assert os.path.isfile(webp_path)
    assert os.path.getsize(webp_path) > 0

    resized_path = media_studio.image_resize_sync(img_path, ws, width=60, height=60, keep_aspect=True)
    assert os.path.isfile(resized_path)

    p1 = os.path.join(ws, "doc1.pdf")
    p2 = os.path.join(ws, "doc2.pdf")
    for p in (p1, p2):
        w = PdfWriter()
        w.add_blank_page(width=200, height=200)
        with open(p, "wb") as fh:
            w.write(fh)

    merged_path = media_studio.pdf_merge_sync([p1, p2], ws, out_name="combined.pdf")
    info = media_studio.pdf_metadata_sync(merged_path)
    assert info["pages"] == 2

    split_path = media_studio.pdf_extract_pages_sync(merged_path, ws, start_page=2, end_page=2)
    assert media_studio.pdf_metadata_sync(split_path)["pages"] == 1

    z_path = media_studio.archive_create_zip_sync([p1, p2], ws, out_name="docs.zip")
    extracted = media_studio.archive_extract_safe_sync(z_path, ws)
    assert len(extracted) == 2

    # Zip-slip path traversal rejection
    evil_zip = os.path.join(ws, "evil.zip")
    with zipfile.ZipFile(evil_zip, "w") as zf:
        zf.writestr("../../outside.txt", "blocked")
    with pytest.raises(media_studio.MediaStudioError):
        media_studio.archive_extract_safe_sync(evil_zip, ws)

    ck = media_studio.file_checksums_sync(p1)
    assert len(ck["sha256"]) == 64
    assert media_studio.file_checksums_sync(p1, expected=ck["sha256"])["verified"] is True


def test_settings_validation_and_scheduled_jobs():
    async def go():
        await reset_db()
        models_db._settings_cache.clear()

        updated = await models_db.update_webapp_settings(9100, {"appearance": {"theme": "light"}, "downloads": {"max_concurrent": 5}})
        assert updated["appearance"]["theme"] == "light"
        assert updated["downloads"]["max_concurrent"] == 5

        with pytest.raises(ValueError):
            await models_db.update_webapp_settings(9100, {"appearance": {"theme": "neon_purple"}})

        job = await models_db.create_scheduled_job(
            user_id=9100,
            kind="download",
            title="Hourly Sync",
            target="https://github.com/octocat/Hello-World",
            run_at_ts=time.time() + 1800,
        )
        assert job["enabled"] is True
        assert await models_db.toggle_scheduled_job(job["job_id"], False, user_id=9100) is True
        assert await models_db.delete_scheduled_job(job["job_id"], user_id=9100) is True

    run(go())
