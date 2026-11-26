#!/usr/bin/env python3
"""Read-only inventory of static clone-engine/group imports for migration planning.

This does not import application modules, connect to services or prove that a
controller is unique. Run from any directory: python scripts/audit_clone_controller.py
"""
import ast
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "VidelBot"
PACKAGE = "filestore.worker_bot"
ENGINE = PACKAGE + ".engine"
GROUP_PREFIX = PACKAGE + ".group_"
IGNORED_DIRS = {"tests", "__pycache__", ".venv", "node_modules"}


def category(module):
    if module == ENGINE or module.startswith(ENGINE + "."):
        return "engine"
    if module.startswith(GROUP_PREFIX):
        return "group"
    return None


def imports_in_source(source, filename):
    """Find explicit imports and constant-string __import__/import_module calls."""
    tree = ast.parse(source, filename=filename)
    found = []

    def record(node, module, kind):
        scope = category(module)
        if scope:
            found.append({"path": filename, "line": node.lineno, "category": scope,
                          "module": module, "form": kind})

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                record(node, alias.name, "import")
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == PACKAGE:
                for alias in node.names:
                    record(node, f"{PACKAGE}.{alias.name}", "from")
            else:
                record(node, node.module, "from")
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            named = (isinstance(func, ast.Name) and func.id in {"__import__", "import_module"})
            attr = isinstance(func, ast.Attribute) and func.attr == "import_module"
            if (named or attr) and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                record(node, node.args[0].value, "dynamic-literal")
    return sorted(found, key=lambda item: (item["path"], item["line"], item["module"]))


def inventory(app=APP):
    app = Path(app)
    if not app.is_dir():
        raise OSError("application directory is unavailable")
    rows, errors = [], []
    for path in sorted(app.rglob("*.py")):
        relative = path.relative_to(app)
        if any(part in IGNORED_DIRS for part in relative.parts):
            continue
        label = "VidelBot/" + relative.as_posix()
        try:
            rows.extend(imports_in_source(path.read_text(encoding="utf-8"), label))
        except (OSError, UnicodeError, SyntaxError) as exc:
            # Avoid echoing source lines or unexpected paths/contents in diagnostics.
            errors.append({"path": label, "error": type(exc).__name__})
    return {"version": 1, "references": rows, "errors": errors,
            "limitations": "Static imports only; aliases, computed imports and indirect callers require manual review. This is not a runtime or concurrency check."}


def main():
    try:
        result = inventory()
    except OSError:
        print("Application source directory is unavailable.", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
