# EMP-Hardened RF Spectrum Monitor for Critical Infrastructure

A production-ready, EMP-resilient RF spectrum monitoring system designed to detect rogue drones, jammers, and other RF threats across **100 MHz – 6 GHz** for critical infrastructure sites including power grids, airports, and government facilities.

---

## Table of Contents

- [Overview](#overview)
- [Key Capabilities](#key-capabilities)
- [Architecture](#architecture)
- [Hardware Requirements](#hardware-requirements)
- [Software Requirements](#software-requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Monitor](#running-the-monitor)
- [Detection Rules](#detection-rules)
- [Alert Integration](#alert-integration)
- [Mesh Deployment](#mesh-deployment)
- [GPS Geo-Tagging](#gps-geo-tagging)
- [Project Structure](#project-structure)
- [Testing](#testing)
- [EMP Hardening Notes](#emp-hardening-notes)

---

## Overview

Post-Ukraine war demand from DOE/FAA and other government agencies has driven the need for robust RF monitoring systems that can:

- **Survive HEMP** (High-altitude Electromagnetic Pulse, 50 kV/m) via Faraday enclosures and mil-spec connectors
- **Detect rogue drones** at 2.4 GHz and 5.8 GHz control/video links
- **Detect GPS jammers** at L1 (1575.42 MHz) and L2 (1227.6 MHz)
- **Detect cellular jammers** in the 700–960 MHz band
- **Flag unknown signals** >–80 dBm persisting for 30 seconds
- **Geo-tag detections** via GPS synchronisation
- **Scale** across 100 km² mesh coverage with up to 50 fixed/mobile units

---

## Key Capabilities

| Feature | Details |
|---|---|
| **Frequency coverage** | 100 MHz – 6 GHz |
| **SDR backends** | RTL-SDR array, USRP B200 (70 MHz – 6 GHz) |
| **Detection threshold** | > –80 dBm, 30-second persistence (configurable) |
| **Signal classes** | Drone (2.4/5.8 GHz), GPS jammer, cellular jammer, VHF ATC, unknown |
| **Alerting** | SNMP v2c traps → NMS / Splunk; Splunk HEC |
| **Geo-tagging** | GPSD daemon, serial NMEA, or static fallback |
| **Mesh scale** | Up to 50 fixed/mobile units, 100 km² coverage |
| **Waterfall display** | ANSI terminal + PNG/CSV export |
| **EMP resilience** | Simulation mode when hardware is absent; designed for Faraday enclosures |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    RF Spectrum Monitor (per unit)                    │
│                                                                     │
│  ┌──────────────┐    ┌──────────────────┐    ┌──────────────────┐  │
│  │  RTL-SDR or  │───▶│ Spectrum Analyzer │───▶│ Signal Detector  │  │
│  │  USRP B200   │    │ (GNU Radio / FFT) │    │ (threshold +     │  │
│  │  (Simulated) │    │ calibration,      │    │  persistence     │  │
│  └──────────────┘    │ smoothing,        │    │  rules)          │  │
│                      │ baseline)         │    └────────┬─────────┘  │
│  ┌──────────────┐    └──────────────────┘             │             │
│  │  GPS Tagger  │───────────────────────────────────▶ │             │
│  │  (GPSD /     │    ┌──────────────────┐             │             │
│  │   NMEA /     │    │   Waterfall      │◀────────────┤             │
│  │   static)    │    │   Display        │             │             │
│  └──────────────┘    └──────────────────┘             │             │
│                                                        ▼             │
│                      ┌──────────────────────────────────────────┐   │
│                      │            Alert Manager                  │   │
│                      │  ┌──────────────┐  ┌──────────────────┐  │   │
│                      │  │  SNMP v2c    │  │  Splunk HEC      │  │   │
│                      │  │  Trap Sender │  │  (JSON POST)     │  │   │
│                      │  └──────────────┘  └──────────────────┘  │   │
│                      └──────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              Mesh Coordinator (optional)                      │  │
│  │  TCP/JSON server receiving reports from up to 50 field units  │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Hardware Requirements

### SDR Hardware

| Component | Details |
|---|---|
| **RTL-SDR** | RTL2832U-based dongle (e.g., RTL-SDR Blog V4); 25 MHz – 1.75 GHz |
| **USRP B200** | Ettus Research USRP B200; 70 MHz – 6 GHz; up to 56 MS/s |
| **Antenna array** | Wideband log-periodic or discone antennas covering 100 MHz – 6 GHz |
| **LNA** | Low-noise amplifier (e.g., LNA4ALL) for weak signal sensitivity |

### EMP Protection

- **Faraday enclosures**: House all electronics in welded steel or copper mesh enclosures (minimum 60 dB shielding effectiveness at 1 GHz)
- **Mil-spec connectors**: Use MIL-DTL-38999 or equivalent threaded connectors for cable ingress
- **TVS diodes**: Transient voltage suppression on all signal and power lines
- **Optical isolation**: Use optical fibre for long control runs between units
- **Battery backup**: Uninterruptible power with surge-protected DC input

### Recommended Deployment

- **50 units** per protected site (mix of fixed towers and mobile platforms)
- **GPS receiver**: Garmin/u-blox M10 on each unit for time and position sync
- **Mesh networking**: 802.11s or dedicated UHF data link between units

---

## Software Requirements

- Python 3.9+
- NumPy ≥ 1.24
- PyYAML ≥ 6.0
- pysnmp ≥ 6.1 (SNMP alerts)
- pyserial ≥ 3.5 (GPS serial)

**Optional (hardware integration):**
- `pyrtlsdr` – RTL-SDR Python bindings
- `uhd` – USRP Hardware Driver (installed via system package manager)
- `gnuradio` – GNU Radio flowgraph support

**Optional (visualisation):**
- `matplotlib` – PNG waterfall export

---

## Installation

```bash
# Clone the repository
git clone https://github.com/DANIELKILONZI/EMP-Hardened-RF-Spectrum-Monitor-for-Critical-Infra
cd EMP-Hardened-RF-Spectrum-Monitor-for-Critical-Infra

# Install Python dependencies
pip install -r requirements.txt

# Install as a package (optional)
pip install -e ".[dev]"

# For RTL-SDR hardware support:
pip install pyrtlsdr

# For GNU Radio (system package – Ubuntu/Debian):
sudo apt-get install gnuradio python3-gnuradio
```

---

## Configuration

Copy and edit the default configuration:

```bash
cp config/default.yaml config/site.yaml
$EDITOR config/site.yaml
```

Key configuration sections:

### Scanners

```yaml
scanners:
  - type: rtlsdr              # or "usrp"
    unit_id: rtlsdr-0
    center_freq_hz: 433000000 # 433 MHz
    sample_rate_hz: 2400000
    gain_db: 30.0
    device_index: 0
    scan_interval_s: 0.1
```

### Detection Rules

```yaml
detection:
  threshold_db: -80.0     # Flag signals above -80 dBm
  persistence_s: 30.0     # Confirm after 30 seconds of persistence
  bin_merge_hz: 500000    # Merge bins within 500 kHz
```

### SNMP Alerts

```yaml
alerts:
  snmp:
    enabled: true
    host: "10.0.0.100"    # Your NMS/Splunk SNMP receiver
    port: 162
    community: "public"
```

### Splunk HEC

```yaml
alerts:
  splunk:
    enabled: true
    hec_url: "https://splunk.example.com:8088/services/collector/event"
    token: "your-hec-token"
    index: "rf_spectrum"
```

### GPS

```yaml
gps:
  serial_port: "/dev/ttyUSB0"  # Serial GPS receiver
  baud_rate: 9600
  # Static fallback if no GPS:
  static_lat: 38.8977
  static_lon: -77.0365
```

---

## Running the Monitor

```bash
# Run with default configuration (simulation mode – no hardware required)
python -m src.monitor

# Run with a custom site configuration
python -m src.monitor --config config/site.yaml

# If installed as a package:
rf-monitor --config config/site.yaml
```

The monitor will:
1. Open all configured SDR scanners (falls back to simulation if hardware absent)
2. Start continuous spectrum scanning
3. Run the signal detection pipeline on each scan
4. Print an ASCII waterfall and status summary every 10 seconds
5. Send SNMP traps / Splunk events when threats are confirmed

---

## Detection Rules

Named rules in `config/detection_rules.yaml` are matched against confirmed signals:

| Rule | Frequency | Threshold | Persistence | Severity |
|---|---|---|---|---|
| GPS L1 jammer | 1560–1590 MHz | –75 dBm | 5 s | CRITICAL |
| GPS L2 jammer | 1215–1240 MHz | –75 dBm | 5 s | CRITICAL |
| Drone control 2.4 GHz | 2400–2485 MHz | –80 dBm | 30 s | WARNING |
| Drone FPV 5.8 GHz | 5725–5875 MHz | –80 dBm | 30 s | WARNING |
| Cellular jammer | 700–960 MHz | –80 dBm | 10 s | CRITICAL |
| VHF ATC interference | 118–137 MHz | –80 dBm | 30 s | CRITICAL |
| ISM 433 MHz rogue | 430–440 MHz | –80 dBm | 30 s | INFO |
| Unknown signal | 100–6000 MHz | –80 dBm | 30 s | WARNING |

---

## Alert Integration

### SNMP v2c Traps

The SNMP sink sends traps to your NMS using the enterprise OID arc `1.3.6.1.4.1.99999.1.1`:

| Sub-OID | Trap | Description |
|---|---|---|
| `.1` | `rfThreatDetected` | Generic threat |
| `.2` | `rfJammerDetected` | Any jammer classification |
| `.3` | `rfDroneDetected` | Drone control/video link |

Variable bindings include: `unit_id`, `description`, `severity`, `peak_power_dbm`, `center_freq_mhz`.

### Splunk SIEM

Events are posted to the Splunk HEC endpoint with `sourcetype: rf:threat`. Create correlation searches on:

```
index=rf_spectrum sourcetype="rf:threat" details.signal_class IN ("gps_jammer","cellular_jammer")
```

---

## Mesh Deployment

Enable the mesh coordinator on the designated aggregation node:

```yaml
mesh:
  enabled: true
  host: "0.0.0.0"
  port: 5555
  max_units: 50
```

Field nodes connect via TCP and send JSON messages:

```json
{ "type": "register", "unit_id": "unit-42", "location": [38.8977, -77.0365, 10.0] }
{ "type": "detection", "unit_id": "unit-42", "data": { "freq_mhz": 2450.0 } }
{ "type": "heartbeat", "unit_id": "unit-42" }
```

The coordinator marks nodes as `DEGRADED` if no message is received for 30 seconds and `OFFLINE` when the connection closes.

---

## GPS Geo-Tagging

Every confirmed detection is stamped with a `(latitude, longitude, altitude_m)` tuple from the local GPS receiver. This enables:

- **Geo-fenced exclusion zones** – ignore known licensed transmitters
- **Multi-unit triangulation** – correlate detections across mesh nodes to localise threats
- **Incident mapping** – export to KML/GeoJSON for situational awareness tools

---

## Project Structure

```
EMP-Hardened-RF-Spectrum-Monitor-for-Critical-Infra/
├── config/
│   ├── default.yaml            # Default system configuration
│   └── detection_rules.yaml    # Named detection rules (frequencies, thresholds)
├── src/
│   ├── monitor.py              # Main orchestrator / CLI entry point
│   ├── scanner/
│   │   ├── base_scanner.py     # Abstract SDR scanner + ScanResult dataclass
│   │   ├── rtlsdr_scanner.py   # RTL-SDR backend (pyrtlsdr / simulation)
│   │   └── usrp_scanner.py     # USRP B200 backend (UHD / simulation)
│   ├── analyzer/
│   │   ├── spectrum_analyzer.py # Calibration, smoothing, history, baseline
│   │   ├── signal_detector.py  # Threshold + persistence detection engine
│   │   └── waterfall.py        # ASCII/PNG/CSV waterfall display
│   ├── alerts/
│   │   ├── alert_manager.py    # De-duplication + multi-sink dispatcher
│   │   ├── snmp_alert.py       # SNMP v2c trap sender (pysnmp)
│   │   └── splunk_alert.py     # Splunk HEC HTTP POST
│   ├── gps/
│   │   └── geo_tagger.py       # GPS location provider (GPSD / NMEA / static)
│   └── mesh/
│       └── mesh_coordinator.py # TCP/JSON mesh server (50-unit support)
├── tests/
│   ├── test_scanner.py         # Scanner and ScanResult tests
│   ├── test_analyzer.py        # Spectrum analyzer, signal detector, waterfall tests
│   ├── test_alerts.py          # Alert manager, SNMP, Splunk tests
│   ├── test_geo_tagger.py      # GeoTagger and NMEA parser tests
│   └── test_mesh.py            # Mesh coordinator tests (TCP integration)
├── requirements.txt
├── setup.py
└── README.md
```

---

## Testing

```bash
# Run the full test suite (no hardware required – uses simulation mode)
pytest tests/ -v

# Run with coverage report
pytest tests/ --cov=src --cov-report=term-missing

# Run a specific module
pytest tests/test_analyzer.py -v
```

All tests run in **simulation mode** – no physical SDR hardware is required.

---

## EMP Hardening Notes

| Layer | Measure |
|---|---|
| **Enclosure** | Welded steel Faraday cage, ≥60 dB shielding @ 1 GHz |
| **Connectors** | MIL-DTL-38999 threaded RF connectors; all cable penetrations via filtered bulkheads |
| **Power** | Opto-isolated DC-DC converters; TVS diode arrays on all power rails |
| **Data** | Fibre optic for inter-unit links >1 m |
| **Grounding** | Single-point chassis ground to site ground grid |
| **Software** | Simulation/fallback mode allows continued operation if hardware is damaged; configurable static GPS fallback |
| **Redundancy** | Mesh architecture: surviving units continue monitoring if others are lost |