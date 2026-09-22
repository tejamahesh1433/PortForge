#!/usr/bin/env bash
# PortForge agent installer (macOS/Linux). Idempotent -- safe to re-run for
# upgrades, reinstalls, or reconfiguration. See docs/installation.md for the
# full guide and docs/v1.1/upgrade.md for the upgrade path this reuses.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install.sh [--central-url URL] [--enrollment-token TOKEN] [--non-interactive]

  --central-url URL         Central server base URL (e.g. http://central-host:58000)
  --enrollment-token TOKEN  Enrollment token minted via:
                             portforge central generate-token --url <central-url>
  --non-interactive         Fail instead of prompting when enrollment info is missing
  -h, --help                Show this help
EOF
}

CENTRAL_URL=""
ENROLLMENT_TOKEN=""
NON_INTERACTIVE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --central-url)
      CENTRAL_URL="${2:-}"
      [ -n "$CENTRAL_URL" ] || { echo "--central-url requires a value" >&2; exit 1; }
      shift 2
      ;;
    --enrollment-token)
      ENROLLMENT_TOKEN="${2:-}"
      [ -n "$ENROLLMENT_TOKEN" ] || { echo "--enrollment-token requires a value" >&2; exit 1; }
      shift 2
      ;;
    --non-interactive)
      NON_INTERACTIVE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON_BIN=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON_BIN="$candidate"
    break
  fi
done
if [ -z "$PYTHON_BIN" ]; then
  echo "Python 3.9+ is required but no python3/python was found on PATH." >&2
  exit 1
fi

PY_OK=$("$PYTHON_BIN" -c 'import sys; print(1 if sys.version_info[:2] >= (3, 9) else 0)')
if [ "$PY_OK" != "1" ]; then
  echo "Python 3.9+ is required; found $("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')." >&2
  exit 1
fi

echo "==> Installing the PortForge agent package (pip install -e ./agent)"
"$PYTHON_BIN" -m pip install -e "$REPO_ROOT/agent" --quiet

if ! command -v portforge >/dev/null 2>&1; then
  echo "portforge was installed but is not on PATH -- check your Python user-scripts/bin directory is on PATH." >&2
  exit 1
fi
portforge --help >/dev/null
echo "==> portforge CLI is installed and on PATH"

echo "==> Installing/reinstalling the native background service"
portforge agent service install

if [ -n "$ENROLLMENT_TOKEN" ]; then
  if [ -z "$CENTRAL_URL" ]; then
    echo "--enrollment-token was given without --central-url." >&2
    exit 1
  fi
  echo "==> Enrolling with Central at $CENTRAL_URL"
  portforge agent enroll --server "$CENTRAL_URL" --token "$ENROLLMENT_TOKEN"
elif [ "$NON_INTERACTIVE" -eq 1 ]; then
  echo "==> Skipping enrollment (--non-interactive, no --enrollment-token given)."
  echo "    Run 'portforge agent enroll --server <url> --token <token>' later."
else
  echo ""
  echo "No enrollment token given. To enroll this host with Central later, run:"
  echo "  portforge agent enroll --server <central-url> --token <token>"
  echo "(mint a token on the Central host with:"
  echo "  portforge central generate-token --url <central-url>)"
  echo ""
fi

echo "==> Starting the native service"
portforge agent service start || echo "    (service start reported an issue -- check 'portforge agent service status')"

echo ""
echo "==> Running portforge doctor"
if [ -n "$CENTRAL_URL" ]; then
  portforge doctor --url "$CENTRAL_URL" || true
else
  portforge doctor || true
fi

echo ""
echo "Install complete. Re-run this script any time to reinstall/upgrade the service in place --"
echo "it never overwrites your host identity or enrollment."
