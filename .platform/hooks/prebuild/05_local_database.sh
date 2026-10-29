#!/bin/bash
set -euo pipefail
python3.12 .platform/scripts/local_database.py setup
if ! rpm -q amazon-ssm-agent >/dev/null 2>&1; then
    dnf install -y amazon-ssm-agent
fi
systemctl enable --now amazon-ssm-agent
