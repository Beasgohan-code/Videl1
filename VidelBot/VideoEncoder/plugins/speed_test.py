"""
Rich Server Speedtest (inspired by WZML-X wzv3-dev plugins/speedtest + Richgram).

Streams live progress stages via ``core.stream.progress``, masks server IP octets
for operational safety, attaches Speedtest.net share cards via WzGram
``LinkPreviewOptions``, and supports one-tap re-runs.
"""
import asyncio
import html
import json

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

from config import ADMINS
from core import rich, stream
from core.rich import Doc
from core.ui import humanbytes, styled_button
from .. import LOGGER, owner, sudo_users

_ALLOWED_USERS = list(dict.fromkeys([*ADMINS, *sudo_users, *owner]))


def mask_ip(ip: str) -> str:
    """Mask last two IPv4 octets (or tail of IPv6) so host IP is never leaked in chat."""
    s = (ip or "").strip()
    if "." in s:
        parts = s.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.x.x"
    if ":" in s:
        parts = s.split(":")
        return ":".join(parts[:2]) + ":xxxx:xxxx"
    return "x.x.x.x"


def _run_python_speedtest() -> dict:
    import speedtest
    st = speedtest.Speedtest(secure=True)
    st.get_best_server()
    st.download()
    st.upload()
    try:
        st.results.share()
    except Exception:
        pass
    return st.results.dict()


async def run_speedtest() -> dict:
    """Run Python speedtest module off the event loop, with subprocess CLI fallback."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(_run_python_speedtest), timeout=180)
    except Exception as first_err:
        proc = await asyncio.create_subprocess_exec(
            "speedtest-cli", "--secure", "--share", "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), 180)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError("Speedtest timed out after 3 minutes") from first_err
        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", "replace").strip() or str(first_err)
            raise RuntimeError(f"Speedtest failed: {err_msg}") from first_err
        return json.loads(stdout.decode("utf-8", "replace"))


def build_speedtest_doc(result: dict) -> tuple[Doc, InlineKeyboardMarkup]:
    dl_bps = float(result.get("download") or 0)
    ul_bps = float(result.get("upload") or 0)
    ping_ms = float(result.get("ping") or 0)
    bytes_recv = result.get("bytes_received") or 0
    bytes_sent = result.get("bytes_sent") or 0
    server = result.get("server") or {}
    client_info = result.get("client") or {}
    share_url = str(result.get("share") or "")

    doc = Doc("🚀", "Server Speedtest", f"{ping_ms:.1f} ms ping · {server.get('sponsor') or 'Speedtest.net'}")
    if share_url.startswith("http"):
        doc.with_preview(share_url)

    metrics = [
        ("📥 Download", f"{humanbytes(dl_bps / 8)}/s ({dl_bps / 1_000_000:.2f} Mbps)"),
        ("📤 Upload", f"{humanbytes(ul_bps / 8)}/s ({ul_bps / 1_000_000:.2f} Mbps)"),
        ("🏓 Latency", f"{ping_ms:.2f} ms"),
    ]
    if bytes_recv or bytes_sent:
        metrics.append(("📊 Data Used", f"↓ {humanbytes(bytes_recv)} · ↑ {humanbytes(bytes_sent)}"))
    doc.kv_table(metrics, key_header="Metric", val_header="Result")

    srv_rows = [
        ("🏢 Sponsor", server.get("sponsor") or "—"),
        ("📍 Location", ", ".join(x for x in (server.get("name"), server.get("country")) if x) or "—"),
        ("📏 Distance", f"{float(server['d']):.1f} km" if server.get("d") is not None else ""),
    ]
    cli_rows = [
        ("🌐 ISP", client_info.get("isp") or "—"),
        ("🛡 Masked IP", rich.code(mask_ip(str(client_info.get("ip") or "")))),
        ("⭐ ISP Rating", str(client_info.get("isprating") or "")),
    ]
    extra = Doc()
    extra.table([(k, v) for k, v in srv_rows + cli_rows if v], header=("Node", "Details"))
    doc.details("🖥 Server & Network Info", extra, open_=True)

    kb_rows = []
    top_row = [styled_button("🔄 Run Again", callback_data="spd_rerun", style="primary")]
    if share_url.startswith("http"):
        top_row.append(Btn("🖼 Result Card", url=share_url))
    kb_rows.append(top_row)
    kb_rows.append([Btn("❌ Close", callback_data="close_btn")])
    return doc, InlineKeyboardMarkup(kb_rows)


@Client.on_message(filters.command("speedtest") & filters.user(_ALLOWED_USERS))
async def speedtest_handler(client: Client, message: Message):
    async with stream.progress(message, "🚀 <i>Selecting best server &amp; running speedtest…</i>", can_stop=True) as prog:
        try:
            await prog.step("🚀 <i>Measuring download &amp; upload throughput…</i>")
            result = await run_speedtest()
            if prog.stopped:
                return
            doc, kb = build_speedtest_doc(result)
            await prog.finish_doc(doc, reply_markup=kb)
        except Exception as e:
            LOGGER.error(f"speedtest failed: {e}")
            await prog.finish(f"❌ Failed running speedtest: <code>{html.escape(str(e))[:250]}</code>")


@Client.on_callback_query(filters.regex(r"^spd_rerun$"))
async def speedtest_rerun_cb(client: Client, query: CallbackQuery):
    uid = query.from_user.id if query.from_user else 0
    if uid not in _ALLOWED_USERS:
        return await query.answer("❌ Admins only.", show_alert=True)
    await query.answer("🚀 Running speedtest…")
    try:
        await query.message.edit_text("🚀 <i>Running fresh speedtest…</i>")
    except Exception:
        pass
    try:
        result = await run_speedtest()
        doc, kb = build_speedtest_doc(result)
        await rich.edit(query.message, doc, reply_markup=kb)
    except Exception as e:
        LOGGER.error(f"speedtest rerun failed: {e}")
        await query.message.edit_text(f"❌ Speedtest failed: <code>{html.escape(str(e))[:250]}</code>")
