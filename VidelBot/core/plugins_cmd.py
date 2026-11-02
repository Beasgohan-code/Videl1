"""
Owner-governed rich control panel for Videl's YAML-manifest Plugin System.

    /plugins                              interactive rich dashboard (list · toggle · reload · info)
    /plugins enable <name>                enable a plugin (owner-only)
    /plugins disable <name>               disable a plugin (owner-only, persisted in MongoDB)
    /plugins reload <name>                hot-reload a plugin module (owner-only)
    /plugins info <name>                  inspect manifest, commands, dependencies & settings
    /plugins set <name> <key> <value>     update a plugin setting (owner-only)
    /plugins install <url|gh:...>         stage & inspect archive/URL/GitHub for explicit owner approval
    /plugins approve <stage_id>           confirm & activate a staged plugin (owner-only)
    /plugins reject <stage_id>            discard a staged plugin (owner-only)
    /plugins uninstall <name>             unload & remove a plugin directory (owner-only)
    /plugins store                        browse remote plugin registry (index.json)
"""
import html
import logging
import os
import tempfile

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

from config import ADMINS, OWNERS
from core import rich
from core.plugin_installer import (
    PluginInstallError,
    StagedPlugin,
    approve_staged,
    fetch_catalog,
    reject_staged,
    stage_from_archive,
    stage_from_source,
    uninstall_plugin,
)
from core.plugin_manager import missing_dependencies, plugin_manager
from core.rich import Doc
from core.ui import styled_button

log = logging.getLogger("videl.plugins_cmd")


def plugins_list_doc() -> tuple[Doc, InlineKeyboardMarkup]:
    items = plugin_manager.summary()
    active = sum(1 for p in items if p["enabled"])
    doc = Doc("🧩", "Plugin Manager", f"{active}/{len(items)} active · YAML manifest engine")
    if not items:
        doc.status("empty", "No modular plugins installed", "Place plugin folders under plugins/")
    else:
        rows = []
        for p in items:
            badge = "🟢 On" if p["enabled"] else "🔴 Off"
            cmds = ", ".join(f"/{c}" for c in p["commands"][:3]) or "—"
            rows.append((
                rich.bold(p["name"]),
                rich.code(p["version"]),
                badge,
                cmds,
            ))
        doc.table(rows, header=("Plugin", "Ver", "State", "Commands"))
    doc.h("⚡", "Owner CLI shortcuts").items([
        rich.Raw("<code>/plugins enable|disable|reload &lt;name&gt;</code>"),
        rich.Raw("<code>/plugins set &lt;name&gt; &lt;key&gt; &lt;value&gt;</code>"),
        rich.Raw("<code>/plugins install gh:owner/repo:plugins/name</code> (stages for owner review)"),
        rich.Raw("<code>/plugins store</code> — browse remote plugin catalog"),
    ])

    kb_rows = []
    for p in items[:12]:
        name = p["name"]
        on = p["enabled"]
        kb_rows.append([
            styled_button(
                f"{'🟢' if on else '🔴'} {name}",
                callback_data=f"plg_mgr:tog:{name}",
                style="success" if on else "danger",
            ),
            styled_button("ℹ️ Info", callback_data=f"plg_mgr:info:{name}", style="primary"),
        ])
    kb_rows.append([Btn("❌ Close", callback_data="close_btn")])
    return doc, InlineKeyboardMarkup(kb_rows)


def plugin_info_doc(name: str) -> tuple[Doc, InlineKeyboardMarkup]:
    plugin_manager.discover()
    m = plugin_manager.manifests.get(name)
    if m is None:
        return Doc("❌", "Plugin not found", name), InlineKeyboardMarkup([[Btn("🔙 Plugins", callback_data="plg_mgr:list")]])
    on = plugin_manager.is_enabled(name)
    miss = missing_dependencies(m.dependencies)
    dep_txt = ", ".join(m.dependencies) if m.dependencies else "None"
    if miss:
        dep_txt += f" (⚠️ missing: {', '.join(miss)})"

    doc = Doc("🧩", f"Plugin · {m.name}", m.description or "modular extension")
    doc.kv_table([
        ("📶 Status", "🟢 Enabled" if on else "🔴 Disabled"),
        ("🏷 Version", rich.code(m.version)),
        ("✍️ Author", m.author),
        ("📄 Entry", rich.code(m.entry)),
        ("📜 Commands", ", ".join(f"/{c}" for c in m.command_names) or "—"),
        ("📦 Dependencies", dep_txt),
        ("🔐 Permissions", ", ".join(m.permissions) or "Standard"),
        ("🏷 Tags", ", ".join(m.tags) if m.tags else None),
        ("🌐 Homepage", rich.link(m.homepage, m.homepage) if m.homepage.startswith("http") else (m.homepage or "—")),
    ], key_header="Field", val_header="Details")

    cfg = plugin_manager.get_plugin_config(name)
    if cfg:
        cfg_doc = Doc().table(
            [(rich.code(k), rich.code(repr(v))) for k, v in sorted(cfg.items())],
            header=("Setting", "Current value"),
        )
        doc.details("⚙️ Plugin settings", cfg_doc, open_=False)

    if m.commands:
        cmd_doc = Doc().table(
            [(f"/{c['name']}", c.get("access", "user"), c.get("description") or "—") for c in m.commands],
            header=("Command", "Access", "Description"),
        )
        doc.details("📜 Registered commands", cmd_doc, open_=True)

    kb = InlineKeyboardMarkup([
        [
            styled_button(
                "🔴 Disable" if on else "🟢 Enable",
                callback_data=f"plg_mgr:tog:{m.name}",
                style="danger" if on else "success",
            ),
            styled_button("🔄 Reload", callback_data=f"plg_mgr:rel:{m.name}", style="primary"),
        ],
        [Btn("🔙 All plugins", callback_data="plg_mgr:list")],
    ])
    return doc, kb


def staged_review_doc(staged: StagedPlugin) -> tuple[Doc, InlineKeyboardMarkup]:
    m = staged.manifest
    doc = Doc("🛡", f"Review Plugin · {m.name}", "staged — no code executed yet")
    risk_txt = (
        "⚠️ " + ", ".join(staged.risk_flags)
        if staged.risk_flags
        else "✅ Clean (standard AST imports & handlers)"
    )
    dep_txt = ", ".join(m.dependencies) if m.dependencies else "None"
    if staged.missing_deps:
        dep_txt += f" · ❌ Missing: {', '.join(staged.missing_deps)}"

    doc.kv_table([
        ("📦 Source", staged.source_label),
        ("🏷 Version", f"{m.version} · by {m.author}"),
        ("📄 Entry", f"{m.entry} ({staged.py_files} .py files · {staged.total_bytes:,} B)"),
        ("🔑 SHA-256", rich.code(staged.sha256[:20] + "…")),
        ("📜 Commands", ", ".join(f"/{c}" for c in m.command_names) or "None"),
        ("🔐 Permissions", ", ".join(m.permissions) or "Standard"),
        ("📦 Dependencies", dep_txt),
        ("🔍 Static Scan", risk_txt),
    ], key_header="Check", val_header="Result")

    if staged.missing_deps:
        doc.status("error", "Missing host dependencies", "Install missing packages before approving.")
    else:
        doc.status("warning", "Owner approval required", "Plugins run inside the bot process. Approve only trusted code.")
    doc.footer(f"Stage ID: {staged.stage_id} · expires in 15 min")

    rows = []
    if not staged.missing_deps:
        rows.append([
            styled_button("✅ Approve & Install", callback_data=f"plg_stg:ok:{staged.stage_id}", style="success"),
            styled_button("❌ Reject", callback_data=f"plg_stg:no:{staged.stage_id}", style="danger"),
        ])
    else:
        rows.append([styled_button("❌ Discard Staged Archive", callback_data=f"plg_stg:no:{staged.stage_id}", style="danger")])
    return doc, InlineKeyboardMarkup(rows)


@Client.on_message(filters.command(["plugins", "plg"]) & filters.user(ADMINS))
async def plugins_cmd(client: Client, message: Message):
    args = (message.command or [])[1:]
    if not args:
        doc, kb = plugins_list_doc()
        return await rich.reply(message, doc, reply_markup=kb)

    sub = args[0].lower()
    uid = message.from_user.id if message.from_user else 0
    is_owner = uid in OWNERS

    if sub in ("enable", "on") and len(args) >= 2:
        if not is_owner:
            return await message.reply_text("❌ Only bot owners can toggle plugins.")
        name = args[1].lower()
        try:
            await plugin_manager.enable(name, client=client)
        except KeyError:
            return await message.reply_text(f"❌ Unknown plugin: <code>{html.escape(name)}</code>")
        doc, kb = plugin_info_doc(name)
        return await rich.reply(message, doc, reply_markup=kb)

    if sub in ("disable", "off") and len(args) >= 2:
        if not is_owner:
            return await message.reply_text("❌ Only bot owners can toggle plugins.")
        name = args[1].lower()
        try:
            await plugin_manager.disable(name, client=client)
        except KeyError:
            return await message.reply_text(f"❌ Unknown plugin: <code>{html.escape(name)}</code>")
        doc, kb = plugin_info_doc(name)
        return await rich.reply(message, doc, reply_markup=kb)

    if sub == "reload" and len(args) >= 2:
        if not is_owner:
            return await message.reply_text("❌ Only bot owners can reload plugins.")
        name = args[1].lower()
        try:
            m = await plugin_manager.reload_plugin(name, client=client)
        except KeyError:
            return await message.reply_text(f"❌ Unknown plugin: <code>{html.escape(name)}</code>")
        except Exception as e:
            return await message.reply_text(f"❌ Reload failed: <code>{html.escape(str(e))}</code>")
        return await message.reply_text(f"🔄 Reloaded plugin <b>{html.escape(m.name)}</b> v<code>{html.escape(m.version)}</code>.")

    if sub == "set" and len(args) >= 4:
        if not is_owner:
            return await message.reply_text("❌ Only bot owners can configure plugins.")
        name, key = args[1].lower(), args[2]
        raw_val = " ".join(args[3:])
        try:
            coerced = await plugin_manager.configure_plugin(name, key, raw_val)
        except (KeyError, ValueError) as e:
            return await message.reply_text(f"❌ <code>{html.escape(str(e))}</code>")
        return await message.reply_text(
            f"✅ Updated <b>{html.escape(name)}</b>.<code>{html.escape(key)}</code> = <code>{html.escape(repr(coerced))}</code>"
        )

    if sub == "info" and len(args) >= 2:
        doc, kb = plugin_info_doc(args[1].lower())
        return await rich.reply(message, doc, reply_markup=kb)

    if sub == "store":
        catalog = await fetch_catalog()
        doc = Doc("🛒", "Plugin Registry", f"{len(catalog)} remote package(s)")
        if not catalog:
            doc.status("empty", "Catalog unavailable", "Could not reach remote index.json right now.")
        else:
            rows = []
            installed = set(plugin_manager.discover())
            for item in catalog[:25]:
                pname = str(item.get("name") or item.get("id") or "")
                ver = str(item.get("version", "1.0.0"))
                state = "✅ Installed" if pname in installed else "📥 Available"
                rows.append((rich.bold(pname), rich.code(ver), state, str(item.get("description", ""))[:60]))
            doc.table(rows, header=("Name", "Ver", "Status", "Description"))
        doc.footer("Stage for review with: /plugins install <name or gh:owner/repo>")
        return await rich.reply(message, doc, reply_markup=InlineKeyboardMarkup([[Btn("🔙 Installed", callback_data="plg_mgr:list")]]))

    if sub == "install":
        if not is_owner:
            return await message.reply_text("❌ Only bot owners can stage and install plugins.")
        rep = message.reply_to_message
        doc_media = getattr(rep, "document", None) if rep else None
        status = await message.reply_text("🔍 <i>Validating &amp; staging plugin archive (no code executed)…</i>")
        try:
            if doc_media and len(args) < 2:
                fname = getattr(doc_media, "file_name", None) or "plugin.zip"
                with tempfile.TemporaryDirectory(prefix="videl_plg_up_") as tmp:
                    dst = os.path.join(tmp, os.path.basename(fname))
                    await client.download_media(rep, file_name=dst)
                    staged = await stage_from_archive(dst, source_label=os.path.basename(fname))
            elif len(args) >= 2:
                staged = await stage_from_source(args[1])
            else:
                return await status.edit_text(
                    "<b>Usage:</b> <code>/plugins install &lt;url | gh:owner/repo[:subdir]&gt;</code> "
                    "or reply to a <code>.zip</code> / <code>.tar.gz</code> plugin archive."
                )
        except PluginInstallError as e:
            return await status.edit_text(f"❌ <b>Validation blocked:</b> <code>{html.escape(str(e))}</code>")
        except Exception as e:
            return await status.edit_text(f"❌ <b>Stage error:</b> <code>{html.escape(str(e))}</code>")
        rev_doc, kb = staged_review_doc(staged)
        return await rich.edit(status, rev_doc, reply_markup=kb)

    if sub == "approve" and len(args) >= 2:
        if not is_owner:
            return await message.reply_text("❌ Only bot owners can approve staged plugins.")
        try:
            manifest = await approve_staged(args[1].strip(), owner_id=uid, client=client)
        except (PermissionError, PluginInstallError) as e:
            return await message.reply_text(f"❌ <code>{html.escape(str(e))}</code>")
        info_doc, kb = plugin_info_doc(manifest.name)
        return await rich.reply(message, info_doc, reply_markup=kb)

    if sub == "reject" and len(args) >= 2:
        if not is_owner:
            return await message.reply_text("❌ Only bot owners can reject staged plugins.")
        ok = reject_staged(args[1].strip())
        return await message.reply_text("🗑 Discarded staged plugin." if ok else "ℹ️ Stage ID already expired or removed.")

    if sub in ("uninstall", "remove") and len(args) >= 2:
        if not is_owner:
            return await message.reply_text("❌ Only bot owners can uninstall plugins.")
        name = args[1].lower()
        try:
            await uninstall_plugin(name, client=client)
        except PluginInstallError as e:
            return await message.reply_text(f"❌ <code>{html.escape(str(e))}</code>")
        return await message.reply_text(f"🗑 Uninstalled plugin <code>{html.escape(name)}</code>.")

    doc, kb = plugins_list_doc()
    return await rich.reply(message, doc, reply_markup=kb)


@Client.on_callback_query(filters.regex(r"^plg_stg:(ok|no):([\w_-]+)$"))
async def plugins_stage_cb(client: Client, query: CallbackQuery):
    uid = query.from_user.id if query.from_user else 0
    if uid not in OWNERS:
        return await query.answer("❌ Only bot owners can approve or reject plugins.", show_alert=True)
    decision, stage_id = query.matches[0].group(1), query.matches[0].group(2)
    if decision == "no":
        reject_staged(stage_id)
        await query.answer("🗑 Staged plugin discarded.")
        doc, kb = plugins_list_doc()
        return await rich.edit(query.message, doc, reply_markup=kb)

    try:
        manifest = await approve_staged(stage_id, owner_id=uid, client=client)
    except (PermissionError, PluginInstallError) as e:
        return await query.answer(f"❌ {str(e)[:180]}", show_alert=True)
    await query.answer(f"✅ Installed {manifest.name} v{manifest.version}!")
    doc, kb = plugin_info_doc(manifest.name)
    return await rich.edit(query.message, doc, reply_markup=kb)


@Client.on_callback_query(filters.regex(r"^plg_mgr:(list|(info|tog|rel):([\w_-]+))$"))
async def plugins_mgr_cb(client: Client, query: CallbackQuery):
    uid = query.from_user.id if query.from_user else 0
    if uid not in ADMINS:
        return await query.answer("❌ Admins only.", show_alert=True)
    action = query.matches[0].group(1)
    if action == "list":
        await query.answer()
        doc, kb = plugins_list_doc()
        return await rich.edit(query.message, doc, reply_markup=kb)

    sub_op = query.matches[0].group(2)
    name = query.matches[0].group(3)
    if sub_op == "info":
        await query.answer()
        doc, kb = plugin_info_doc(name)
        return await rich.edit(query.message, doc, reply_markup=kb)

    if uid not in OWNERS:
        return await query.answer("❌ Only bot owners can modify plugins.", show_alert=True)

    if sub_op == "tog":
        try:
            now_on = not plugin_manager.is_enabled(name)
            await plugin_manager.set_enabled(name, now_on, client=client)
        except KeyError:
            return await query.answer("Plugin not found.", show_alert=True)
        await query.answer(f"{'🟢 Enabled' if now_on else '🔴 Disabled'} {name}")
        cur_txt = (query.message.text or "") if query.message else ""
        if "Plugin Manager" in cur_txt or "𝗣𝗟𝗨𝗚𝗜𝗡 𝗠𝗔𝗡𝗔𝗚𝗘𝗥" in cur_txt:
            doc, kb = plugins_list_doc()
        else:
            doc, kb = plugin_info_doc(name)
        return await rich.edit(query.message, doc, reply_markup=kb)

    if sub_op == "rel":
        try:
            m = await plugin_manager.reload_plugin(name, client=client)
        except Exception as e:
            return await query.answer(f"Reload failed: {e}"[:190], show_alert=True)
        await query.answer(f"🔄 Reloaded {m.name} v{m.version}")
        doc, kb = plugin_info_doc(name)
        return await rich.edit(query.message, doc, reply_markup=kb)
