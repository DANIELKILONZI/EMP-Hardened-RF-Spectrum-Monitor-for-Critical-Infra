# HEMP Survivability & EMC Test Report Template
## EMP-Hardened RF Spectrum Monitor for Critical Infrastructure

**Document Number:** RF-MON-TEST-001  
**Revision:** 1.0  
**Classification:** UNCLASSIFIED // FOR OFFICIAL USE ONLY  
**Applicable Standards:** MIL-STD-461G, MIL-STD-462D, IEC 61000-4 series, IEEE C63.12  

---

## 1. Introduction

### 1.1 Purpose
This document provides a structured test checklist and reporting template for verifying
the electromagnetic compatibility (EMC) and high-altitude electromagnetic pulse (HEMP)
survivability of the EMP-Hardened RF Spectrum Monitor system deployed at critical
infrastructure sites (power grids, airports, government facilities).

### 1.2 Scope
Tests cover all hardware assemblies including:
- SDR receiver units (RTL-SDR, USRP B200)
- Raspberry Pi 4 / industrial SBC compute nodes
- Faraday enclosures and RF-shielded cabinets
- Power conditioning modules (EMP-hardened UPS)
- Antenna systems and RF cables/connectors (MIL-C-17 / SMA)
- Ethernet and serial communications cabling

### 1.3 Definitions

| Abbreviation | Definition |
|---|---|
| HEMP | High-Altitude Electromagnetic Pulse (nuclear detonation at ≥30 km altitude) |
| EMP | Electromagnetic Pulse |
| E1 | Fast EMP component (rise time < 5 ns, peak ~50 kV/m) |
| E2 | Intermediate EMP component (similar to lightning, 1 µs – 1 s) |
| E3 | Slow EMP component (geomagnetic-storm-like, 0.1 – 300 s) |
| EMC | Electromagnetic Compatibility |
| SPD | Surge Protective Device |
| TEM Cell | Transverse Electromagnetic Cell (test fixture) |

---

## 2. Test Configuration

### 2.1 Unit Under Test (UUT)

| Field | Value |
|---|---|
| System Name | EMP-Hardened RF Spectrum Monitor |
| Hardware Version | _______ |
| Software Version | _______ |
| Serial Number | _______ |
| Test Date | _______ |
| Test Facility | _______ |
| Test Engineer | _______ |
| Witness (government) | _______ |

### 2.2 Test Equipment

| Item | Manufacturer / Model | Calibration Due |
|---|---|---|
| EMP Simulator (E1) | _______ | _______ |
| Surge Generator (E2) | _______ | _______ |
| Waveform Monitor | _______ | _______ |
| Spectrum Analyzer | _______ | _______ |
| TEM Cell | _______ | _______ |
| Shielding Effectiveness Probe | _______ | _______ |
| Power Quality Analyzer | _______ | _______ |

---

## 3. MIL-STD-461G Compliance Checklist

### 3.1 Conducted Emissions (CE)

| Test ID | Requirement | Limit | Measured | Pass/Fail | Notes |
|---|---|---|---|---|---|
| CE101 | Power leads, 30 Hz – 10 kHz | Class A limits | _______ dBµA | ☐ P ☐ F | |
| CE102 | Power leads, 10 kHz – 10 MHz | Class A limits | _______ dBµV | ☐ P ☐ F | |
| CE106 | Antenna port, 10 kHz – 40 GHz | –60 dBm max | _______ dBm | ☐ P ☐ F | |

### 3.2 Conducted Susceptibility (CS)

| Test ID | Requirement | Test Level | Result | Pass/Fail | Notes |
|---|---|---|---|---|---|
| CS101 | Power leads, 30 Hz – 150 kHz | 3 V | Operational ☐ | ☐ P ☐ F | |
| CS106 | Transient susceptibility | 400 V, 5 µs | Operational ☐ | ☐ P ☐ F | |
| CS114 | Bulk current injection, 10 kHz – 200 MHz | Level 5 | Operational ☐ | ☐ P ☐ F | |
| CS115 | Bulk current injection (spike) | 2 A, 1 ns | Operational ☐ | ☐ P ☐ F | |
| CS116 | Damped sinusoidal transients | 0.1 – 100 MHz | Operational ☐ | ☐ P ☐ F | |

### 3.3 Radiated Emissions (RE)

| Test ID | Requirement | Limit | Measured Peak | Pass/Fail | Notes |
|---|---|---|---|---|---|
| RE101 | Magnetic field, 30 Hz – 100 kHz | 24 dBpT | _______ | ☐ P ☐ F | |
| RE102 | Electric field, 2 MHz – 18 GHz | Class A limits | _______ dBµV/m | ☐ P ☐ F | |

### 3.4 Radiated Susceptibility (RS)

| Test ID | Requirement | Test Level | Result | Pass/Fail | Notes |
|---|---|---|---|---|---|
| RS101 | Magnetic field, 30 Hz – 100 kHz | 183 dBpT | Operational ☐ | ☐ P ☐ F | |
| RS103 | Electric field, 10 kHz – 40 GHz | 10 V/m | Operational ☐ | ☐ P ☐ F | |
| RS105 | Transient EM field (HEMP E1 surrogate) | 50 kV/m, 2.5 ns | Operational ☐ | ☐ P ☐ F | |

---

## 4. IEC 61000-4 Compliance Checklist

### 4.1 Immunity Tests

| Standard | Test | Level | Criterion | Pass/Fail | Notes |
|---|---|---|---|---|---|
| IEC 61000-4-2 | ESD | Level 4 (8 kV contact, 15 kV air) | B | ☐ P ☐ F | |
| IEC 61000-4-3 | Radiated RF immunity | Level 3 (10 V/m, 80–1000 MHz) | A | ☐ P ☐ F | |
| IEC 61000-4-4 | EFT/Burst | Level 4 (4 kV, 2.5 kHz) | B | ☐ P ☐ F | |
| IEC 61000-4-5 | Surge | Level 4 (4 kV/2 kA, 1.2/50 µs) | B | ☐ P ☐ F | |
| IEC 61000-4-6 | Conducted RF | Level 3 (10 V, 150 kHz – 80 MHz) | A | ☐ P ☐ F | |
| IEC 61000-4-8 | Power-freq magnetic field | Level 5 (100 A/m) | A | ☐ P ☐ F | |
| IEC 61000-4-9 | Pulse magnetic field | Level 5 (1000 A/m) | A | ☐ P ☐ F | |
| IEC 61000-4-11 | Voltage dips & interrupts | Class III | B | ☐ P ☐ F | |
| IEC 61000-4-34 | Voltage dips (HV equip.) | Class III | B | ☐ P ☐ F | |

**Performance Criteria:**
- **A** – Normal operation during and after test
- **B** – Temporary degradation, self-recovery
- **C** – Temporary degradation, operator intervention required

---

## 5. HEMP-Specific Survivability Tests

### 5.1 E1 Fast Transient (Nuclear EMP)

| Parameter | Requirement | Measured | Pass/Fail |
|---|---|---|---|
| Peak electric field | 50 kV/m | _______ kV/m | ☐ P ☐ F |
| Rise time (10–90%) | ≤ 2.5 ns | _______ ns | ☐ P ☐ F |
| FWHM pulse width | 25 ns | _______ ns | ☐ P ☐ F |
| System operational after exposure | Yes | ☐ Yes ☐ No | ☐ P ☐ F |
| No component damage | Yes | ☐ Yes ☐ No | ☐ P ☐ F |
| Boot recovery time | ≤ 60 s | _______ s | ☐ P ☐ F |

**Notes / Observations:**
```
____________________________________________________________
____________________________________________________________
```

### 5.2 Faraday Enclosure Shielding Effectiveness

| Frequency | Required SE (dB) | Measured SE (dB) | Pass/Fail |
|---|---|---|---|
| 100 kHz | ≥ 60 dB | _______ | ☐ P ☐ F |
| 1 MHz | ≥ 70 dB | _______ | ☐ P ☐ F |
| 10 MHz | ≥ 80 dB | _______ | ☐ P ☐ F |
| 100 MHz | ≥ 80 dB | _______ | ☐ P ☐ F |
| 1 GHz | ≥ 60 dB | _______ | ☐ P ☐ F |

### 5.3 SPD / Transient Suppression Verification

| Port | SPD Type | Clamping Voltage | Let-Through Energy | Pass/Fail |
|---|---|---|---|---|
| AC mains input | MOV + Gas Discharge | _______ V | _______ J | ☐ P ☐ F |
| DC power rail | TVS array | _______ V | _______ J | ☐ P ☐ F |
| Ethernet (PoE) | RJ45 surge module | _______ V | _______ J | ☐ P ☐ F |
| RF antenna (coax) | Gas discharge (antenna) | _______ V | — | ☐ P ☐ F |
| Serial GPS | TVS + common-mode choke | _______ V | _______ J | ☐ P ☐ F |

### 5.4 E3 / Geomagnetic Disturbance (Power Quality)

| Test | Standard | Level | Result | Pass/Fail |
|---|---|---|---|---|
| Sustained overvoltage | IEC 61000-4-11 | +20 % for 5 s | Operational ☐ | ☐ P ☐ F |
| Sustained undervoltage | IEC 61000-4-11 | –40 % for 10 s | Operational ☐ | ☐ P ☐ F |
| Complete power interruption | IEC 61000-4-11 | 250 ms | Self-recover ☐ | ☐ P ☐ F |
| UPS hold-up time | Site requirement | ≥ 30 min | _______ min | ☐ P ☐ F |

---

## 6. Hardware Hardening Inspection Checklist

### 6.1 Enclosure & Mechanical

- [ ] Faraday enclosure uses continuous welded seams (no gaps > 1 mm)  
- [ ] All cable penetrations use EMI-rated feedthroughs (MIL-DTL-38999 or equivalent)  
- [ ] Enclosure door/lid uses beryllium-copper or Ni-Ag mesh finger-strip gaskets  
- [ ] Ventilation openings use waveguide-below-cutoff honeycomb panels  
- [ ] Enclosure bonded to facility ground bus (< 0.1 Ω impedance verified)  
- [ ] MIL-C-17 or LMR-400 coaxial cable with SMA/N-type connectors throughout  

### 6.2 Power Conditioning

- [ ] Isolation transformer on AC mains input (Class B or better EMP filter)  
- [ ] SPD installed at mains entry (Type 1 + Type 2 combination, IEC 61643-11)  
- [ ] DC power rail has ≥ 100 V TVS protection on all lines  
- [ ] Capacitor bank for E1 energy absorption verified (≥ 1 µF, 200 V rating)  
- [ ] UPS battery backup tested for 30+ min hold-up at full system load  
- [ ] Power conditioner MIL-STD-1275E (28 VDC) compliant (if mobile deployment)  

### 6.3 Communications & Interfaces

- [ ] Ethernet ports protected with surge modules (IEEE 802.3 rated)  
- [ ] Serial GPS port protected with TVS + common-mode choke  
- [ ] SNMP/mesh TCP communication uses shielded CAT6A or fibre optic  
- [ ] Antenna lightning/EMP arrestor installed at feedline entry (< 1 m from enclosure)  
- [ ] All connectors cleaned, torqued to specification, and weatherproofed  

### 6.4 Software Resilience

- [ ] System auto-restarts all services after power restore (systemd `Restart=on-failure`)  
- [ ] SQLite database uses WAL journal mode (no corruption on abrupt power loss)  
- [ ] GPS falls back to static coordinate if GPS receiver loses fix  
- [ ] Scanner falls back to simulation mode if hardware fails post-EMP  
- [ ] Alert manager retries failed SNMP/Splunk sends with exponential backoff  
- [ ] Config file validated on startup; bad config does not crash monitor  

---

## 7. DOE/FAA Site Acceptance Criteria

| Criterion | Target | Actual | Accepted |
|---|---|---|---|
| System detects –80 dBm signal within 30 s | ≤ 30 s | _______ s | ☐ Yes ☐ No |
| Threat detection rate (GPS jammer sim) | ≥ 99 % | _______ % | ☐ Yes ☐ No |
| False positive rate (24 h baseline) | ≤ 1 per day | _______ / day | ☐ Yes ☐ No |
| Web dashboard availability | ≥ 99.9 % / week | _______ % | ☐ Yes ☐ No |
| System uptime after E1 exposure | Self-recover ≤ 60 s | _______ s | ☐ Yes ☐ No |
| Mesh network recovery after power cycle | ≤ 120 s for all nodes | _______ s | ☐ Yes ☐ No |
| SNMP trap latency (detection → NMS) | ≤ 5 s | _______ s | ☐ Yes ☐ No |

---

## 8. Test Results Summary

| Test Category | Total Tests | Passed | Failed | Deferred |
|---|---|---|---|---|
| MIL-STD-461G CE | 3 | | | |
| MIL-STD-461G CS | 5 | | | |
| MIL-STD-461G RE | 2 | | | |
| MIL-STD-461G RS | 3 | | | |
| IEC 61000-4 Series | 9 | | | |
| HEMP E1 Survivability | 6 | | | |
| Faraday SE | 5 | | | |
| SPD Verification | 5 | | | |
| DOE/FAA Acceptance | 7 | | | |
| **TOTAL** | **45** | | | |

**Overall Result:** ☐ PASS   ☐ CONDITIONAL PASS   ☐ FAIL

---

## 9. Non-Conformances and Corrective Actions

| NCR # | Test ID | Description | Root Cause | Corrective Action | Due Date | Status |
|---|---|---|---|---|---|---|
| 001 | | | | | | |
| 002 | | | | | | |

---

## 10. Signatures

| Role | Name | Signature | Date |
|---|---|---|---|
| Test Engineer | | | |
| Systems Engineer | | | |
| Government Test Witness | | | |
| Program Manager | | | |

---

## Appendix A – Reference Documents

| Document | Title |
|---|---|
| MIL-STD-461G | Requirements for the Control of Electromagnetic Interference |
| MIL-STD-464C | Electromagnetic Environmental Effects Requirements for Systems |
| MIL-HDBK-419A | Grounding, Bonding, and Shielding for Electronic Systems |
| IEC 61000-4-2 | Electrostatic Discharge Immunity |
| IEC 61000-4-3 | Radiated, Radio-Frequency, Electromagnetic Field Immunity |
| IEC 61000-4-4 | Electrical Fast Transient / Burst Immunity |
| IEC 61000-4-5 | Surge Immunity |
| IEC 61000-4-25 | HEMP Immunity – Conducted Disturbance |
| IEEE C63.12 | Recommended Practice for EMC Limits |
| FEMA P-1019 | Essential Critical Infrastructure Workers |
| DOE/EH-0173T | Electromagnetic Pulse Protection Guidelines |
