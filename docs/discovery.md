# Discover OTT-play controllers in the local network

The service type is `_ottplay-ctrl._tcp`. Clients use their network search
domains, such as `home.arpa`, rather than a domain compiled into the player.
This project-specific service name is not currently registered with IANA;
registration is needed to reserve it globally before treating it as a public
standard. The records follow DNS-SD's PTR, SRV and TXT structure.

```dns
_ottplay-ctrl._tcp.home.arpa. IN PTR home._ottplay-ctrl._tcp.home.arpa.
home._ottplay-ctrl._tcp.home.arpa. IN SRV 0 0 443 controller.example.com.
home._ottplay-ctrl._tcp.home.arpa. IN TXT "txtvers=1" "scheme=https" "path=/ott-control"
```

Use a single ASCII instance label, such as `home`, and a hostname with a trusted
TLS certificate as the SRV target. The HTTPS base URL is assembled from the
target, port and path. Unknown versions, unsafe paths and malformed records are
rejected. A secret must never appear in these records. Multiple controllers are
presented for selection; a client must not select one arbitrarily.

## OpenWrt

The installer adds exactly three named `dnsrr` UCI sections. It stages a copy of
the DHCP configuration, validates record syntax with dnsmasq, refuses pending
or concurrent DHCP edits, and saves a private router backup before installation.
DNS restarts briefly to load the new records. Other DNS settings and DHCP leases
are preserved. The router must support OpenWrt's `dnsrr` sections.

```sh
# Print a reviewable script first.
python3 scripts/openwrt-discovery.py --router router --domain home.arpa \
  --address https://controller.example.com/ott-control

# Apply after reviewing the address and domain.
python3 scripts/openwrt-discovery.py --router router --domain home.arpa \
  --address https://controller.example.com/ott-control --apply
```

The domain should be supplied to devices by DHCP. `.local` is reserved for
multicast DNS and is not accepted by this unicast installer. Backups are saved
under `/root/ottplay-dns-backups` on the router. To remove only this feature,
delete `dhcp.ottplay_ctrl_ptr`, `dhcp.ottplay_ctrl_srv` and
`dhcp.ottplay_ctrl_txt`, then commit DHCP and restart dnsmasq after checking for
unrelated pending edits. Do not restore an old full backup over later changes.

## Server discovery bridge

The command server can expose DNS metadata to browsers that cannot query DNS
records themselves. Add the following optional block to its private config:

```json
{
  "discovery": {
    "domain": "home.arpa",
    "nameserver": "192.168.1.1:53",
    "public_url": "https://controller.example.com/ott-control"
  }
}
```

The values above are illustrative. `public_url` must match a controller address
advertised in DNS. On a container deployment, explicitly set the home router and
domain so cluster DNS does not discover a different network. Omitting domain or
nameserver uses `/etc/resolv.conf` for that field; use explicit values on Windows
or when the server's DNS configuration cannot represent the desired network.
Omitting the entire block disables discovery and pairing routes.

`GET /api/discovery` is public metadata with the existing exact CORS policy.
The hosted bridge returns only descriptors matching its configured `public_url`,
including the port and path; unrelated DNS advertisements cannot nominate a
different controller through this trusted HTTPS endpoint.
Configure the deployed browser profile's `window.__OTT_CONTROL_DISCOVERY_URL__`
to this HTTPS endpoint. This profile explicitly chooses the bridge's network;
it does not automatically inspect the network of a roaming browser. Tauri and
the local OTT server can use their native DNS access instead. Failure to discover
a controller must not block playback or unrelated player functions. Existing
manual configuration and deliberate disconnection are preserved.

Native discovery trusts the network administrator to nominate controllers through
DNS. TLS authenticates the nominated hostname, not its ownership by the user.
Use automatic native discovery on trusted home or managed networks; keep a saved
manual controller on untrusted networks. Pairing approval protects credentials
issued by the legitimate controller, but cannot authenticate an arbitrary server
that controls its own approval responses.

## Pairing

An unconfigured registered player generates a pairing request at the discovered
controller and displays its eight-character code. An administrator approves the
matching player and code with `ott approve NAME CODE`. Only the request's private
claim secret can retrieve that player's existing device token. UUID knowledge,
DNS metadata and the display code alone cannot retrieve a token. The admin token
is never sent to the player.

Pairing expires after ten minutes, is bounded and rate-limited, and does not
survive a server restart. A changed DNS descriptor invalidates approval or
credential delivery. The frontend saves approved settings before discarding the
claim secret; cancellation is best effort with expiry as a fallback. See
[the API contract](api.md) for endpoints and [the CLI guide](cli.md) for commands.
