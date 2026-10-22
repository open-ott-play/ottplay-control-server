"""Render/check the 24-language links in complete documentation families."""

import argparse
import json
import re
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = json.loads((ROOT / "docs/languages.json").read_text(encoding="utf-8"))
FAMILIES = (
    "README.md",
    "deploy/android-usb-manager/README.md",
    "docs/android-kiosk-setup.md",
    "docs/android-ota.md",
    "docs/android-usb-manager.md",
    "docs/reliable-control.md",
    "docs/wiki/Home.md",
)
START = "<!-- language-navigation:start -->"
END = "<!-- language-navigation:end -->"


def localized(source: Path, code: str) -> Path:
    suffix = "" if code == "en" else f".{code}"
    return source.with_name(f"{source.stem}{suffix}.md")


def navigation(source: Path) -> str:
    links = [
        f'<a href="{localized(source, item["code"]).name}" '
        f'lang="{item["code"]}" dir="{item.get("dir", "ltr")}">'
        f"{escape(item['label'])}</a>"
        for item in LANGUAGES
    ]
    rows = [" · ".join(links[i : i + 8]) for i in range(0, len(links), 8)]
    return f'{START}\n<p dir="ltr">\n' + "<br>\n".join(rows) + f"\n</p>\n{END}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check without writing")
    args = parser.parse_args()
    changes = []
    errors = []
    for family in FAMILIES:
        source = ROOT / family
        expected = navigation(source)
        for item in LANGUAGES:
            path = localized(source, item["code"])
            if not path.is_file():
                errors.append(f"Missing translation: {path.relative_to(ROOT)}")
                continue
            body = path.read_text(encoding="utf-8")
            if body.count(START) != 1 or body.count(END) != 1:
                errors.append(
                    f"Expected one navigation block: {path.relative_to(ROOT)}"
                )
                continue
            pattern = re.escape(START) + r".*?" + re.escape(END)
            updated, count = re.subn(
                pattern, lambda _, rendered=expected: rendered, body, flags=re.DOTALL
            )
            if count != 1:
                errors.append(f"Invalid navigation markers: {path.relative_to(ROOT)}")
            elif updated != body:
                changes.append((path, updated))
    if errors:
        print("\n".join(errors))
        return 1
    if args.check and changes:
        print(
            "Stale language navigation; run python3 docs/render_language_navigation.py"
        )
        return 1
    if not args.check:
        for path, updated in changes:
            path.write_text(updated, encoding="utf-8")
    print(f"Validated {len(FAMILIES) * len(LANGUAGES)} pages and their language links.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
