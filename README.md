<img src="https://raw.githubusercontent.com/ColeMundus/ha-spectrum/main/custom_components/spectrum/brand/icon.png" alt="Spectrum" width="96" height="96">

# Spectrum for Home Assistant

Monitor and manage your Spectrum residential **Account**, **Networks**, **Routers**, **Modems**, **Pods**, **Set-Top Boxes**, **Clients**, **Outages**, **Service Appointments**, and **Billing Summary** in Home Assistant.

Independent and unofficial integration; not affiliated with or endorsed by Charter Communications or Spectrum. Bundled geometric brand artwork is original and MIT-licensed.

## Installation

Requires **Home Assistant Core 2026.10.0 or later**.

### HACS (recommended)

1. Open **HACS** in Home Assistant (or use [Open in HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=ColeMundus&repository=ha-spectrum&category=integration)).
2. Search for **Spectrum** in the HACS default store.
3. Download **Spectrum** and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration → Spectrum**.

### HACS custom repository

If **Spectrum** does not appear in your HACS catalog search:

1. Open **HACS → Custom repositories**.
2. Add `https://github.com/ColeMundus/ha-spectrum` with category **Integration**.
3. Download **Spectrum** and restart Home Assistant.

### Manual installation

1. Copy `custom_components/spectrum` into your Home Assistant configuration's `custom_components` folder (or extract a release ZIP at your Home Assistant configuration root).
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Spectrum**.

## Authentication and setup

Each config entry represents one Spectrum residential **Account** and automatically discovers all associated **Networks**, **Routers**, **Modems**, **Pods**, **Set-Top Boxes**, and **Clients**.

Spectrum's OAuth2 + PKCE browser sign-in flow is used for initial setup, **Reconfigure**, and **Reauthentication**:

1. Start **Add integration → Spectrum** in Home Assistant.
2. Open the displayed `id.spectrum.net` sign-in URL in your browser and complete sign-in (including any MFA or CAPTCHA prompts).
3. After sign-in, your browser redirects to a `msaauth://www.spectrum.net/sign-in-redirect?code=...` URL (your browser may show an "unsupported protocol" or blank page—this is expected).
4. Copy the full `msaauth://...` redirect URL (or just the `code` query parameter) from the browser address bar and paste it into Home Assistant.
5. Submit the form to exchange the authorization code for OAuth tokens and discover your **Account** and hardware.

OAuth tokens are stored in Home Assistant's config entry storage and refreshed automatically before expiry. If tokens are revoked or expire permanently, Home Assistant prompts you through its native **Reauthentication** flow using the same browser sign-in step.

## Supported devices and entities

Home Assistant creates devices for your **Account** hub and physical hardware units (**Router**, **Modem**—merged into one device for combo units—each **Pod**, and each **Set-Top Box**). **Clients** attach as entities under their parent **Router** to keep the Device Registry clean. Entities are created only when supported by your hardware and plan.

| Platform | Scope | Entities |
| --- | --- | --- |
| **Binary Sensor** | **Account**, **Router**, **Modem**, **Pod**, **Set-Top Box** | Active **Outage**, **Outage** credit eligible/enrolled, **Outage** callback eligible, autopay, past due, seasonal suspend, **Service Appointment** alert, hardware & **Modem** connectivity, **Security Shield** threats active, **Additional Network** active, cellular backup ready/active, T4 timeout threshold, Ethernet port speed, speed achievable, hot-listed, quarantined |
| **Sensor** | **Account**, **Network**, **Router**, **Modem**, **Pod**, **Set-Top Box** | **Outage** count/status/message/services affected/estimated resolution/cause; **Service Appointment** next/status/count; **Billing Summary** (current balance, amount due, past-due balance, pending payment, last payment, upcoming autopay charge, next autopay date, payment due date, next billing cycle, account status, billing status, provisioned services); internet plan & subscribed download/upload speeds; speed test download/upload/latency/jitter/packet loss/last run/status; **Wi-Fi Coverage** score; connected/known/paused **Clients**; **IP Reservation** & **Port Forward** counts; **Security Shield** threats; WAN IPv4/IPv6 & LAN IPv4; DNS mode & primary/secondary DNS; **Wi-Fi Network** & **Additional Network** names and passwords (password sensors disabled by default); connection state, firmware version, DOCSIS capability, cellular backup signal/usage/status, Thread channel, **Pod** connected **Clients** & backhaul details |
| **Switch** | **Network**, **Router**, **Client Group**, **Client** | **Security Shield**, UPnP, Spectrum Wi-Fi hotspot, Thread radio, **Client Group** pause switches (enabled by default), individual **Client** pause switches (disabled by default) |
| **Button** | **Account**, **Router**, **Modem** | Reboot **Router**, reboot **Modem**, run **Router** speed test, run **Modem** speed test, smart steer **Clients**, **Account** refresh |
| **Select** | **Pod** | **Pod** LED mode (`normal`, `locate`) |
| **Image** | **Router** | Local QR code for the primary **Wi-Fi Network** and **Additional Network** (rendered locally via `segno`) |
| **Device Tracker** | **Router** (**Clients**) | **Client** presence tracking, enabled by default for connected or IP-reserved **Clients** (configurable in Options) |

Factory reset is deliberately excluded for safety and cannot be triggered from Home Assistant.

## Actions

The integration registers 17 actions under the `spectrum` domain:

| Category | Actions |
| --- | --- |
| **Port Forwards** | `spectrum.add_port_forward`, `spectrum.update_port_forward`, `spectrum.delete_port_forward`, `spectrum.list_port_forwards` (returns response data) |
| **IP Reservations** | `spectrum.set_ip_reservation`, `spectrum.delete_ip_reservation` |
| **Access Schedules** | `spectrum.set_access_schedule` |
| **DNS & Wi-Fi Networks** | `spectrum.set_dns`, `spectrum.update_wifi`, `spectrum.set_additional_network`, `spectrum.delete_additional_network` |
| **Clients & Client Groups** | `spectrum.rename_client`, `spectrum.remove_client`, `spectrum.create_client_group`, `spectrum.update_client_group`, `spectrum.delete_client_group` |
| **Speed Tests** | `spectrum.run_speed_test` (`ROUTER`, `MODEM_DOWNLOAD`, or `MODEM_UPLOAD`, with optional `wait`) |

## Options and tiered polling

Open the integration's **Configure** menu to adjust:

- **Network and Client polling interval** (default `60s`, range `30–600s`): **Network**, **Clients**, **Client Groups**, **Security Shield**, and **Wi-Fi Coverage**.
- **Hardware, Outage, and Service Appointment polling interval** (default `300s`, range `60–3600s`): **Router**, **Modem**, **Pod**, **Set-Top Box**, **Outages**, and **Service Appointments**.
- **Billing Summary and plan polling interval** (default `3600s`, range `300–86400s`): **Billing Summary** and subscribed internet plan.
- **Client tracker default enablement**: choose between **Connected or IP-reserved Clients** (default), **All discovered Clients**, or **Disabled by default**.

## Privacy and diagnostics

- Download diagnostics from **Settings → Devices & services → Spectrum → Download diagnostics**.
- Diagnostics automatically redact OAuth access/refresh/ID tokens, **Wi-Fi Network** passphrases, account numbers, MAC addresses, serial numbers, WAN IP addresses, and subscriber contact details.
- **Wi-Fi Network** and **Additional Network** password sensors are disabled by default in the Entity Registry so credentials are only exposed if explicitly enabled.
- Config entry unique IDs hash account identifiers with SHA-256 so raw account numbers are never stored as Home Assistant unique IDs.
- All communication goes directly between your Home Assistant instance and Spectrum's official endpoints (`id.spectrum.net` and `apis.spectrum.net`) via [`aiospectrum`](https://github.com/ColeMundus/aiospectrum).

## Vocabulary

Canonical domain terms (**Account**, **Outage**, **Service Appointment**, **Billing Summary**, **Network**, **Router**, **Modem**, **Pod**, **Set-Top Box**, **Wi-Fi Network**, **Additional Network**, **Wi-Fi Coverage**, **Client**, **Client Group**, **Access Schedule**, **IP Reservation**, **Port Forward**, **Security Shield**) are defined in [GLOSSARY.md](GLOSSARY.md).

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md) for local development and testing, and [SECURITY.md](SECURITY.md) for private vulnerability reporting. Licensed under the [MIT License](LICENSE).
