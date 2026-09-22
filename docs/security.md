# Security Model

PortForge v1.0.0 is designed with a trust model suitable for local development environments and private organizational networks.

## Core Security Assumptions

### 1. Local/Private Network Control Plane
Central is designed to operate within a trusted LAN, VPN, or local Docker network. **Do not expose Central directly to the public internet.** Use network firewalls to restrict access to trusted host agents and developer machines.

### 2. Enrollment and Identity
Host agents must authenticate with Central using an enrollment token (generated via `portforge central
generate-token --url <central-url>`, which itself requires the Central host's admin bootstrap token
(`PORTFORGE_ADMIN_BOOTSTRAP_TOKEN`) -- only whoever holds that secret can mint new host enrollment tokens).
This token issues a permanent, host-specific cryptographic credential used to sign subsequent snapshot
transmissions and requests. An attacker cannot submit data for a host without its specific credential.

### 3. Dashboard Accessibility
By design, the PortForge Dashboard does not require browser authentication. This is an intentional choice for friction-free developer experience inside a private network. Anyone with access to the Dashboard port can view project topologies and clear conflicts.

### 4. No Arbitrary Manifest Execution
PortForge CLI safely parses YAML manifests. It does not execute arbitrary code or scripts embedded within the manifest.

### 5. Path-Bound Config Mutation
When PortForge applies ports to configuration files (`.env`, `docker-compose.yml`), mutations are bound to the explicit project directory.

### 6. Safe Logging
PortForge logs sanitize sensitive metadata. Tokens and cryptographic keys are never written to disk in plain text outside of the secure local credential store, nor are they printed to terminal outputs or dashboards.

### 7. Remote Bind-Probe Limitations
Currently, Central cannot authoritatively probe remote hosts to verify binding statuses in real-time. It relies on the periodic transmission of telemetry from authenticated agents. Stale hosts (agents that have not checked in recently) will have their data flagged, and Central will conservatively assume their last-known reservations remain active.
