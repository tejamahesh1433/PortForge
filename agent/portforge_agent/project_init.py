from pathlib import Path
from typing import Iterable, Optional
import yaml
from .manifest import ManifestError, validate_manifest

def parse_port_definition(value: str) -> tuple[str, dict]:
    parts=[part.strip() for part in value.split(":")]
    if len(parts) not in (2,3) or not parts[0] or not parts[1]:
        raise ManifestError("MANIFEST_INIT_INVALID_PORT", f"Invalid --port '{value}'; expected name:purpose[:protocol].")
    protocol=parts[2] if len(parts)==3 else "tcp"
    if protocol not in ("tcp","udp"):
        raise ManifestError("MANIFEST_INIT_INVALID_PORT", f"Invalid --port '{value}'; protocol must be tcp or udp.")
    return parts[0], {"purpose":parts[1],"protocol":protocol}

def build_manifest(project: str, host: str, ports: Iterable[str]) -> dict:
    port_map={}
    for value in ports:
        name,entry=parse_port_definition(value)
        if name in port_map: raise ManifestError("MANIFEST_INIT_INVALID_PORT", f"Duplicate --port name '{name}'.")
        port_map[name]=entry
    data={"version":1,"project":project,"target":{"host":host},"ports":port_map}; validate_manifest(data); return data

def render_manifest(project: str, host: str, ports: Iterable[str]) -> str:
    return yaml.safe_dump(build_manifest(project,host,ports),sort_keys=False)

def create_manifest(project: str, host: str, ports: Iterable[str], output: Optional[Path]=None) -> Path:
    destination=output or Path.cwd()/"portforge.yml"
    if destination.exists(): raise ManifestError("MANIFEST_ALREADY_EXISTS",f"Refusing to overwrite existing manifest: {destination}")
    try:
        destination.parent.mkdir(parents=True,exist_ok=True); destination.write_text(render_manifest(project,host,ports),encoding="utf-8",newline="\n")
    except OSError as exc: raise ManifestError("MANIFEST_WRITE_FAILED",f"Could not write manifest '{destination}': {exc}") from exc
    return destination
