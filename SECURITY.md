# Security Policy

## Reporting a vulnerability

Report security vulnerabilities privately through GitHub's [private vulnerability reporting](https://github.com/ColeMundus/ha-spectrum/security/advisories/new) instead of opening a public issue.

## Protecting your credentials

- Never post OAuth access tokens, refresh tokens, ID tokens, `msaauth://` redirect URLs, authorization codes, **Wi-Fi Network** passphrases, account numbers, MAC addresses, serial numbers, or unredacted Home Assistant configuration backups in issues or pull requests.
- Use Home Assistant's **Download diagnostics** output (which automatically redacts sensitive fields) and verify no personal or secret data remains before sharing.
- Protect your Home Assistant `.storage` directory and backups, as OAuth tokens for your Spectrum **Account** are stored in Home Assistant config entry storage.
- If you suspect your session tokens were exposed, sign out or change your Spectrum account password at `spectrum.net` and reauthenticate in Home Assistant.
