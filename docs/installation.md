# Installation Guide

This guide covers the prerequisites and installation steps for PortForge Central and the Host Agents.

## Prerequisites
- **Python**: 3.10 or higher.
- **Docker**: (Optional but recommended) Required if you want PortForge to scan container bindings or run the Central services via Docker.
- **PostgreSQL**: Central requires a PostgreSQL database.

---

## 1. Central Setup
Central should be installed on a single machine or server in your network.

**Using Docker Compose (Recommended)**
```bash
git clone https://github.com/tejamahesh1433/PortForge.git
cd PortForge
docker-compose up -d
```
This will spin up PostgreSQL, the FastAPI backend on port 8000, and the Next.js Dashboard on port 3000.

---

## 2. Host Agent Setup

The Host Agent must be installed on every machine (developer laptop, server) that you want to manage.

### Windows

Install the CLI and background service natively:
```powershell
pip install -e ./agent
portforge-agent install
portforge-agent start
```

### macOS

Install natively using pip:
```bash
pip install -e ./agent
portforge-agent install
portforge-agent start
```

### Linux

Install natively using pip:
```bash
pip install -e ./agent
portforge-agent install
portforge-agent start
```

## 3. Host Enrollment
Once an agent is installed and running, you must enroll it with Central so it can begin reporting active ports.

Generate a token on Central (or use an existing enrollment token), then on the agent machine:
```bash
portforge enroll --central "http://central-ip:8000" --token "YOUR_TOKEN"
```
The agent will now securely transmit its scan data and accept CLI commands to allocate ports.
