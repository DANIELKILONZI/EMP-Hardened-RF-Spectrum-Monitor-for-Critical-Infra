# ============================================================
# EMP-Hardened RF Spectrum Monitor – Production Dockerfile
# ============================================================
# Multi-stage build:
#   Stage 1 (builder) – install Python deps into a venv
#   Stage 2 (runtime) – minimal image with the venv copied in
#
# Build:
#   docker build -t emp-rf-monitor:latest .
#
# Run (simulation mode, no hardware):
#   docker run --rm -p 8080:8080 emp-rf-monitor:latest
#
# Run with real RTL-SDR (Linux USB passthrough):
#   docker run --rm --privileged -v /dev/bus/usb:/dev/bus/usb \
#     -p 8080:8080 emp-rf-monitor:latest
# ============================================================

# ---- Stage 1: builder ------------------------------------------------
FROM python:3.11-slim AS builder

# Install build dependencies for C-extensions (numpy, pyserial, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libusb-1.0-0-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy dependency manifests first to exploit Docker layer caching
COPY requirements.txt setup.py ./
COPY src/__init__.py src/__init__.py

# Create a virtualenv and install all runtime deps
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip wheel && \
    /opt/venv/bin/pip install --no-cache-dir \
        numpy>=1.24.0 \
        pyyaml>=6.0 \
        pysnmp>=6.1.0 \
        pyserial>=3.5 \
        flask>=3.0.0 \
        matplotlib>=3.7.0

# ---- Stage 2: runtime -----------------------------------------------
FROM python:3.11-slim AS runtime

# Runtime USB library needed by pyrtlsdr / USRP UHD
RUN apt-get update && apt-get install -y --no-install-recommends \
    libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for security hardening
RUN useradd -m -u 1000 rfmonitor
USER rfmonitor

WORKDIR /app

# Copy virtualenv from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source
COPY --chown=rfmonitor:rfmonitor src/ src/
COPY --chown=rfmonitor:rfmonitor config/ config/

# Dashboard HTTP port
EXPOSE 8080

# SNMP trap receiver port (UDP 162 requires root – handled by host NMS)
# EXPOSE 162/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/status')" || exit 1

ENTRYPOINT ["python", "-m", "src.monitor"]
CMD ["--config", "config/default.yaml"]
