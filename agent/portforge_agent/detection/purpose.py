"""Rule-based purpose/category detection.

Rules are centralized here and deliberately conservative: a bare
interpreter (``python.exe``, ``node.exe``, a bare ``java``) never implies a
purpose on its own. Every rule requires a specific, *named* piece of
evidence: an exact known-software identity match, a dependency actually
declared in a manifest, a distinctive command-line tool name, or an
unambiguous Docker Compose service-name keyword.

Evidence sources and conflict resolution
-----------------------------------------
Four independent sources are checked, always in this fixed order (so results
are 100% deterministic for the same input, never "whichever rule happened to
run first"):

  1. process name / container image identity (``KNOWN_SOFTWARE``)
  2. a dependency actually declared in a project manifest
     (``NODE_FRAMEWORK_DEPENDENCIES`` / ``PYTHON_FRAMEWORK_DEPENDENCIES``)
  3. a distinctive tool name in the command line (``COMMAND_LINE_HINTS``)
  4. an unambiguous keyword in a Docker Compose service name
     (``COMPOSE_SERVICE_KEYWORDS``)

Each source that fires produces one ``Signal`` (category, purpose, evidence
string). If every signal agrees on the same category, confidence is HIGH
(one signal alone is already enough -- e.g. a process literally named
``mysqld.exe`` is decisive on its own) and every agreeing piece of evidence
is recorded together. If signals disagree on category, this is a genuine
conflict: the category with the most supporting signals wins at a
*downgraded* MEDIUM confidence (all evidence, including the disagreeing
signal, is kept so the conflict stays visible); an exact tie between
categories is left as ``unknown`` at LOW confidence rather than guessing.
No evidence at all -> ``unknown`` at UNKNOWN confidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from .models import Confidence, DetectionFacts, PurposeDetectionResult

# (category, purpose) for an exact, normalized process-name or container-
# image identity match. Keys are lowercase, with a trailing ".exe" already
# stripped by _normalize_process_identity / _normalize_image_identity.
KNOWN_SOFTWARE: Dict[str, Tuple[str, str]] = {
    "postgres": ("database", "postgresql"),
    "postgresql": ("database", "postgresql"),
    "mysqld": ("database", "mysql"),
    "mysql": ("database", "mysql"),
    "mariadbd": ("database", "mariadb"),
    "mongod": ("database", "mongodb"),
    "redis-server": ("cache", "redis"),
    "redis": ("cache", "redis"),
    "memurai": ("cache", "memurai"),
    "nginx": ("reverse-proxy", "nginx"),
    "traefik": ("reverse-proxy", "traefik"),
    "caddy": ("reverse-proxy", "caddy"),
    "grafana": ("monitoring", "grafana"),
    "grafana-server": ("monitoring", "grafana"),
    "prometheus": ("metrics", "prometheus"),
    "ollama": ("ai", "ollama"),
    "minio": ("object-storage", "minio"),
    "rabbitmq-server": ("message-broker", "rabbitmq"),
    "kafka": ("message-broker", "kafka"),
}

# Dependency name (as it appears in package.json dependencies) -> (category, purpose).
NODE_FRAMEWORK_DEPENDENCIES: Dict[str, Tuple[str, str]] = {
    "next": ("frontend", "nextjs"),
    "vite": ("development-server", "vite"),
    "express": ("api", "express"),
    "react-scripts": ("development-server", "react-dev-server"),
}

# Dependency name (pyproject.toml / requirements.txt) -> (category, purpose).
PYTHON_FRAMEWORK_DEPENDENCIES: Dict[str, Tuple[str, str]] = {
    "fastapi": ("api", "fastapi"),
    "flask": ("api", "flask"),
    "django": ("api", "django"),
}

# Distinctive command-line substrings (checked case-insensitively against
# the joined command line) -> (category, purpose). Deliberately excludes
# generic wrapper phrases like "npm run dev"/"npm start" -- too common to
# carry real information, which is exactly the over-triggering this design
# is meant to avoid (see "Be conservative" in the module docstring).
COMMAND_LINE_HINTS: Dict[str, Tuple[str, str]] = {
    "uvicorn": ("api", "api"),
    "gunicorn": ("api", "api"),
    "flask": ("api", "flask"),
    "manage.py": ("api", "django"),
    "django-admin": ("api", "django"),
    "vite": ("development-server", "vite"),
    "next dev": ("frontend", "nextjs"),
    "next start": ("frontend", "nextjs"),
    "react-scripts": ("development-server", "react-dev-server"),
    "webpack-dev-server": ("development-server", "webpack-dev-server"),
    "nginx": ("reverse-proxy", "nginx"),
}

# (keyword, category, purpose), checked in this fixed order against a
# lowercased Compose service name; only the FIRST match is used per
# service, so more specific keywords are listed before more generic ones.
COMPOSE_SERVICE_KEYWORDS: List[Tuple[str, str, str]] = [
    ("frontend", "frontend", "frontend"),
    ("backend", "api", "api"),
    ("api", "api", "api"),
    ("gateway", "reverse-proxy", "reverse-proxy"),
    ("proxy", "reverse-proxy", "reverse-proxy"),
    ("nginx", "reverse-proxy", "nginx"),
    ("postgres", "database", "postgresql"),
    ("mysql", "database", "mysql"),
    ("mongo", "database", "mongodb"),
    ("database", "database", "database"),
    ("redis", "cache", "redis"),
    ("cache", "cache", "cache"),
    ("minio", "object-storage", "minio"),
    ("storage", "object-storage", "object-storage"),
    ("grafana", "monitoring", "grafana"),
    ("monitor", "monitoring", "monitoring"),
    ("prometheus", "metrics", "prometheus"),
    ("metrics", "metrics", "metrics"),
    ("queue", "message-broker", "message-broker"),
    ("broker", "message-broker", "message-broker"),
    ("worker", "infrastructure", "worker"),
    ("ui", "frontend", "frontend"),
    ("web", "frontend", "frontend"),
]


@dataclass
class Signal:
    category: str
    purpose: str
    evidence: str


def _normalize_process_identity(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    normalized = name.strip().lower()
    if normalized.endswith(".exe"):
        normalized = normalized[:-4]
    return normalized or None


def _normalize_image_identity(image: Optional[str]) -> Optional[str]:
    if not image:
        return None
    base = image.split("/")[-1]  # drop registry/org path
    base = base.split("@", 1)[0]  # drop digest
    base = base.split(":", 1)[0]  # drop tag
    return base.strip().lower() or None


def _collect_signals(facts: DetectionFacts, manifest_dependencies: Set[str]) -> List[Signal]:
    signals: List[Signal] = []

    # 1. process / image identity
    proc_id = _normalize_process_identity(facts.process_name)
    if proc_id and proc_id in KNOWN_SOFTWARE:
        category, purpose = KNOWN_SOFTWARE[proc_id]
        signals.append(Signal(category, purpose, f"process name indicates {purpose} ({facts.process_name})"))

    image_id = _normalize_image_identity(facts.container_image)
    if image_id and image_id in KNOWN_SOFTWARE:
        category, purpose = KNOWN_SOFTWARE[image_id]
        signals.append(
            Signal(category, purpose, f"container image indicates {purpose} ({facts.container_image})")
        )

    # 2. manifest dependency
    for dep in sorted(manifest_dependencies):
        if dep in NODE_FRAMEWORK_DEPENDENCIES:
            category, purpose = NODE_FRAMEWORK_DEPENDENCIES[dep]
            signals.append(Signal(category, purpose, f"manifest dependency '{dep}' indicates {purpose}"))
        if dep in PYTHON_FRAMEWORK_DEPENDENCIES:
            category, purpose = PYTHON_FRAMEWORK_DEPENDENCIES[dep]
            signals.append(Signal(category, purpose, f"manifest dependency '{dep}' indicates {purpose}"))

    # 3. command line (native process, or the container's resolved command)
    command_tokens = facts.command_line or facts.container_command or []
    command_text = " ".join(str(t) for t in command_tokens).lower()
    if command_text:
        for hint, (category, purpose) in COMMAND_LINE_HINTS.items():
            if hint in command_text:
                signals.append(Signal(category, purpose, f"command line contains '{hint}'"))

    # 4. Docker Compose service name
    if facts.compose_service:
        service_lower = facts.compose_service.lower()
        for keyword, category, purpose in COMPOSE_SERVICE_KEYWORDS:
            if keyword in service_lower:
                signals.append(
                    Signal(
                        category,
                        purpose,
                        f"Compose service name '{facts.compose_service}' suggests {purpose}",
                    )
                )
                break  # only the highest-priority keyword match per source

    return signals


def _resolve(signals: List[Signal]) -> PurposeDetectionResult:
    if not signals:
        return PurposeDetectionResult()

    categories = {s.category for s in signals}
    if len(categories) == 1:
        category = signals[0].category
        purpose = signals[0].purpose  # first signal in fixed source-precedence order
        method = "rule_based_agreement" if len(signals) > 1 else "rule_based"
        return PurposeDetectionResult(
            purpose=purpose,
            category=category,
            confidence=Confidence.HIGH,
            method=method,
            evidence=[s.evidence for s in signals],
        )

    # Conflicting categories: deterministic majority tie-break, never "first rule wins".
    votes: Dict[str, int] = {}
    for s in signals:
        votes[s.category] = votes.get(s.category, 0) + 1
    max_votes = max(votes.values())
    leading = [category for category, count in votes.items() if count == max_votes]

    if len(leading) == 1:
        category = leading[0]
        purpose = next(s.purpose for s in signals if s.category == category)
        return PurposeDetectionResult(
            purpose=purpose,
            category=category,
            confidence=Confidence.MEDIUM,  # downgraded: some evidence disagreed
            method="rule_based_majority",
            evidence=[s.evidence for s in signals],
        )

    # A genuine, unresolved tie between categories.
    return PurposeDetectionResult(
        purpose=None,
        category="unknown",
        confidence=Confidence.LOW,
        method="conflicting_evidence",
        evidence=[s.evidence for s in signals],
    )


def detect_purpose(
    facts: DetectionFacts, manifest_dependencies: Optional[Set[str]] = None
) -> PurposeDetectionResult:
    signals = _collect_signals(facts, manifest_dependencies or set())
    return _resolve(signals)
