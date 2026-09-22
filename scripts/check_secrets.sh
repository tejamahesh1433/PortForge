#!/bin/bash
gitleaks detect --no-git --redact || { echo "Secrets detected!"; exit 1; }
echo "No secrets detected."
