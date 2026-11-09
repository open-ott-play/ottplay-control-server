# Native catalog selection and subtitle preferences

These commands require a compatible CLI, controller and native player. Discover
the target's live capabilities with `ott lg-native caps`; a provider name or
installed version alone does not establish support. Source tests do not establish
that a particular television has received the native build.

## Random item from the current category

```sh
ott lg-native categories
ott lg-native category "Family movies"
ott lg-native s --limit 10
ott lg-native random --receipt
ott lg-native random --receipt "Tom and Jerry"
ott lg-native,other-native random --receipt
```

`random --receipt [SEARCH]` explicitly selects uniformly from the complete
snapshot of the current browsing category and optional title filter. The player
can select a row beyond the first returned page. The command does not change the
provider, category, list order or saved settings. The current item can be chosen
again. A group resolves each player's own category and receipt independently.
Place `--receipt` after `random` for this selection mode. The existing global
`ott --receipt PLAYER COMMAND` option instead prints command receipt IDs.

This mode requires the exact advertised capability
`catalog_random: {"version":2,"scope":"receipt","request_field":"receipt_random"}`
and readable categories. The CLI reads a channel-page receipt and submits one
`play_catalog` request containing that receipt and
`receipt_random: {"version":2,"scope":"receipt"}`. Category-list receipts are
insufficient. The snapshot and discovery must be complete, even though only one
page needs to be transferred.

The player enforces its normal parental, kiosk and playback restrictions. This
mode makes no safe-content classification, AceStream exclusion or channel-number
weighting promise. Use a deliberately chosen category when opting in. Ordinary
`R` / `random` retain their existing safe selection and weighting contract; they
require the separate legacy `random` capability. Adding `--receipt` explicitly
chooses the native mode. Numeric range arguments cannot be combined with it.

An empty, loading, expired or changed catalog is rejected. A refused item is not
replaced with another draw. A timeout or invalid acknowledgement does not cause a
second playback request or a fallback to the legacy protocol. A successful
response confirms dispatch, not a rendered video frame.

## Native subtitle selection

Discover the settings actually offered by the target:

```sh
ott lg-native settings schema
ott lg-native setting nativeSubtitleMode
ott lg-native setting nativeSubtitleMode auto
ott lg-native setting nativeSubtitleMode forced
ott lg-native setting nativeSubtitleMode off
ott lg-native setting nativeSubtitleLanguage en
ott lg-native setting nativeSubtitleLanguage pt-BR
ott lg-native setting nativeSubtitleLanguage ""
```

Compatible native builds advertise application-scoped string settings. `off`
uses manual selection and is the default; `auto` follows the native automatic
selection policy; `forced` considers only tracks explicitly marked FORCED.
An empty preferred language uses the manifest preference. These settings do not
change interface language or Plex account preferences. A manual track or Off
choice for the current item retains precedence over automatic policy.

The native language policy accepts an empty string or a bounded language-tag
subset: at most 35 ASCII bytes, two or three initial letters, followed by
hyphen-separated components of one to eight letters or digits. The CLI uses the
advertised type and limits; the player remains authoritative for language
validation. Unsupported values such as `en_US` are rejected without automatic
correction or write replay.

Each save first reads the live descriptor, context token and opaque revision,
then sends one conditional write. Subtitle mode, language and appearance edits
can share a revision domain. The CLI never constructs a numeric revision or
reuses one from an earlier command. See [typed settings](player-settings.md) for
private value files, output and failure handling.

## Configure an explicit native provider profile

Native Edem and registered operators expose a separately negotiated, conditional
configuration workflow. Discover the advertised providers and version 1 fields:

```sh
ott lg-native provider-config --schema
ott lg-native provider-config --schema edem
ott lg-native provider-config --schema ottclub
```

Keep credentials in a private JSON file. For example, `/private/edem.json`:

```json
{"provider":"edem","number":3,"settings":{"key":"YOUR_KEY","list":2}}
```

Replace `YOUR_KEY` locally and protect the file with mode `600` on macOS/Linux
or equivalent Windows permissions. `number` selects saved profile slot 3;
`list:2` selects the provider's third playlist template. They are separate
choices. Other operators use their exact advertised ID and named fields such
as `server`, `username`, `password`, `playlist`, `mac`, `list`, `mode`; only
their version 1 field schema is accepted. Edem uses `key`, `list`, `portal`.

The player also applies its provider-specific rules. Shura's `server` accepts
the string `1`, `2`, `3` or `5` (empty means the default `1`), not a URL.
OTTCLUB requires a bare host with an optional port, without a scheme, path,
query, fragment or userinfo. Invalid values produce a fixed diagnostic that
does not expose credentials.

```sh
ott lg-native provider-config --check /private/edem.json
ott lg-native provider-config /private/edem.json
ott lg-native,other-native provider-config /private/edem.json
```

The three-key file `{provider,number,settings}` selects the guarded native
workflow. Existing two-key provider configuration files retain their legacy
workflows. Bound files use canonical provider IDs, including `edem`; the
`ilook`/`ilooktv` aliases belong to provider selection commands.

`--check` compares the supplied partial settings without saving. An ordinary
save first negotiates controller and player support, reads a comparison receipt
for that exact provider/slot/patch, and saves once only if different. Unspecified
fields stay unchanged. Native codec defaults participate in comparison, so a
default-normalized value does not force repeated writes.

An inactive slot stays inactive. Saving it does not switch providers, select a
profile, start playback or wake standby. A changed active slot can reload after
confirmed saving; an identical configuration does not reload. A concurrent
provider/profile/source edit invalidates the receipt. The command never guesses
the active slot, retries a lost save or falls back to an unguarded write.

Replies contain only public operation metadata, never credentials or current
configuration values. Group operations use each target's own fresh receipt and
report each outcome independently; successful saves are not rolled back when
another target fails. Unknown persistence must be inspected before a new attempt.
See the [bound configuration contract](design/native-provider-settings-v1.md)
for the exact allowlist, bounds, response schema and admission rules.

### Include native credentials in `load Local`

Add an optional `native_provider_settings` array inside an existing private
preset, alongside its required `m3u`, `plex` and `active_profile` fields:

```json
"native_provider_settings": [
  {"provider":"edem","number":3,"settings":{"key":"YOUR_KEY","list":2}},
  {"provider":"shura","number":1,"settings":{"server":"1","username":"YOUR_USER","password":"YOUR_PASSWORD"}},
  {"provider":"ottclub","number":2,"settings":{"server":"portal.example:8080","username":"YOUR_USER","password":"YOUR_PASSWORD"}}
]
```

This is a fragment to add to `presets.Local`, not a replacement for `cli.json`.
Use only providers and slots you intend to configure; each canonical
provider/slot pair must be unique. Existing presets do not gain invented
credentials and need no format changes when this optional array is absent.

```sh
ott lg-native load Local plan
ott lg-native load Local
ott lg-native load Local verify
ott lg-native load Local apply
ott lg-native load Local resume
```

`plan` validates the local document and prints public step names without contacting
the player. Ordinary loading and resumable `apply` negotiate and compare every
native entry before the first preset mutation. Each native write obtains a fresh
receipt immediately before its conditional save; preflight receipts are never
reused. Final verification compares canonical settings again. Checkpoints retain
operation identity and progress without credential values or reusable native
write receipts. Resume first reconciles pending work and compares desired state;
it never blindly replays an unacknowledged save.

Native writes preserve inactive slots and do not activate their provider. The
complete preset retains its existing final M3U `active_profile` behavior.
Presets containing a saved Favorites companion retain the separate
[`--settings-only` requirement](cli.md#named-setups); this extension does not
silently skip Favorites or other unsupported preset sections. See
[named setups](cli.md#named-setups) for group execution, checkpoint paths and
partial-failure handling.

## Edem Favorites identity

Native Edem/iLook Favorites keep `origin.provider` equal to `edem`. Live entries
carry their configured playlist-template name, such as `Playlist 3`, as
`origin.subprofile`; this is separate from the numbered saved profile slot.
Videos from the configured embedded VPortal retain their actual category, such
as `Movies/Comedy`, in that same subprofile field. Portable entries are inspected
with the existing negotiated command:

```sh
ott lg-native fav global current
```

An embedded portal item uses only the attested
`{kind:"vportal-media",idField,id}` locator. No URL, credentials or extra path
field is accepted. The CLI and controller preserve the exact provider and
subprofile; they do not rename Edem to M3U/VPortal or derive playlist templates
from saved profile slot numbers. Portable metadata is not playback authority:
the receiving player must still verify its configured source and exact binding.

## Troubleshooting

- **Capability unavailable:** inspect `caps` and update the missing component.
  The CLI does not substitute legacy random playback on native LG.
- **Catalog loading or empty:** wait for discovery or inspect `s` and the chosen
  category/filter before issuing a new command. No partial population is drawn.
- **Catalog changed or expired:** inspect the current category and source before
  deliberately issuing a new request. The previous mutation is not replayed.
- **Settings unavailable or read-only:** use the live descriptor's access and
  reason fields; a command cannot override local restrictions.
- **Native provider configuration unsupported:** require the negotiated
  `native_provider_settings` marker and controller probe, and use an explicit
  numbered file with a canonical advertised provider. Do not remove the number
  to work around a failed conditional write.
- **`revision_conflict`, `source_changed` or `runtime_changed`:** read the setting
  again and decide whether a new write is still appropriate.
- **Playback outcome uncertain:** inspect `status` before another playback
  request. Dispatch acknowledgement alone does not establish visible playback.
