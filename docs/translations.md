# Documentation translations

The following documentation families have complete editions in the same 24
languages as the [ha-homelab community documentation](https://github.com/ha-homelab/.github):

- [Project README](../README.md)
- [Android USB deployment package](../deploy/android-usb-manager/README.md)
- [Android kiosk setup](android-kiosk-setup.md)
- [Android OTA](android-ota.md)
- [Shared Android USB manager](android-usb-manager.md)
- [Reliable control and playback handoff](reliable-control.md)
- [Documentation index](wiki/Home.md)

English is the canonical technical source. Each edition covers the complete
source, including prerequisites, failure modes, security boundaries, limitations,
and acceptance evidence. Russian editions are maintained to the same standard.
Other guides linked from these pages may still be English-only. The versioned
index does not automatically publish GitHub Wiki pages. These are documentation
languages, not a claim of application UI language support.

## Update an edition

1. Update the English source and every translated edition in that family. Keep
   commands, option names, paths, identifiers, URLs, versions, hashes, and recorded
   test counts exact. Translate prose and image descriptions; preserve the
   distinction between completed validation and work that remains unverified.
2. Use the sibling filename convention: `guide.md` for English and
   `guide.<code>.md` for translations. Language codes and native labels are in
   [languages.json](languages.json); Chinese uses `zh-Hans` and `zh-Hant`.
3. Keep one `language-navigation` marker block per page. Arabic, Persian and
   Hebrew use right-to-left prose; navigation and fenced commands remain
   left-to-right. Preserve blank lines around HTML wrappers so GitHub renders
   the enclosed Markdown.
4. Refresh navigation, then check it without writing:

   ```sh
   python3 docs/render_language_navigation.py
   python3 docs/render_language_navigation.py --check
   git diff --check
   ```

The renderer checks all 168 pages and 4,032 sibling language links before writing.
It fails on missing pages or invalid markers; it never translates or rewrites
prose. Review commands, sections, relative links, and the rendered RTL pages as
well: valid navigation does not establish translation accuracy. When adding a
new complete family, extend `FAMILIES` in the renderer only after all editions
exist. Do not silently replace full translations with abbreviated landing pages.
