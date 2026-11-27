# Spectrum Home Assistant Integration

A Home Assistant integration for monitoring and managing a Spectrum residential **Account**, its home **Network** hardware, **Clients**, **Outages**, and **Billing Summary**.

## Language

### Account, Outages & Billing

**Account**:
A Spectrum residential subscriber account represented by a single Home Assistant config entry.
_Avoid_: Customer, profile, subscription

**Outage**:
An active or resolved service disruption or impairment reported by Spectrum for an **Account** (including affected services and estimated resolution time).
_Avoid_: Incident, alert, downtime

**Service Appointment**:
A scheduled technician visit or proactive maintenance alert associated with an **Account**.
_Avoid_: Work order, ticket, dispatch

**Billing Summary**:
The current balance, amount due, past-due balance, due date, autopay status, and billing cycle dates for an **Account**.
_Avoid_: Invoice, ledger, statement

### Hardware & Networks

**Network**:
A home network under an **Account**, containing its **Router**, **Clients**, **Security Shield**, and hotspot settings.
_Avoid_: LAN, CPN, customer premises network

**Router**:
A Spectrum-managed Wi-Fi router belonging to a **Network**.
_Avoid_: Gateway, equipment, node

**Modem**:
A Spectrum DOCSIS unit providing WAN connectivity under an **Account**, distinct from a **Router** (a combo unit combines both capabilities in one physical unit).
_Avoid_: Cable modem, DOCSIS device

**Pod**:
A Spectrum Wi-Fi mesh extender unit paired with a **Router**.
_Avoid_: Extender, satellite, mesh node

**Set-Top Box**:
A Spectrum TV receiver box associated with an **Account**.
_Avoid_: Cable box, receiver, STB

**Wi-Fi Network**:
A wireless network broadcast by a **Router**, either the primary network or an **Additional Network**.
_Avoid_: SSID, access point, WLAN

**Additional Network**:
A secondary **Wi-Fi Network** configured on a **Router** alongside its primary **Wi-Fi Network**.
_Avoid_: Guest network, secondary SSID

**Wi-Fi Coverage**:
Spectrum's current and historical signal coverage quality score for a **Network**, its **Clients**, and its **Pods**.
_Avoid_: Signal score, mesh health

### Clients, Access Control & Forwarding

**Client**:
A machine known to a **Network** (such as a phone, laptop, or server), uniquely identified by its MAC address.
_Avoid_: Device, host, endpoint

**Client Group**:
A named collection of **Clients** managed together for pause state and **Access Schedules**.
_Avoid_: Device group, profile, household group

**Access Schedule**:
A recurring time window during which internet access is paused for a **Client** or **Client Group**.
_Avoid_: Bedtime, parental schedule, timer

**IP Reservation**:
A fixed IPv4 address bound to a **Client** on a **Router**; serves as the required parent for **Port Forwards**.
_Avoid_: DHCP reservation, static IP, lease

**Port Forward**:
A single-port IPv4 forwarding entry mapping an external port to an internal port and protocol on a **Client**'s **IP Reservation**.
_Avoid_: Port assignment, port mapping, rule, NAT rule

### Security

**Security Shield**:
Spectrum's router-level threat-blocking toggle on a **Network**.
_Avoid_: Firewall, antivirus, threat protection
