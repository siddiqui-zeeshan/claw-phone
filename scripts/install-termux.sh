#!/bin/bash
# Install dependencies for claw-phone on Termux
#
# Run this script once after cloning the repo:
#   bash scripts/install-termux.sh

set -euo pipefail

echo "=== claw-phone Termux installer ==="
echo ""

# 1. Update Termux packages
echo "[1/4] Updating Termux packages..."
pkg update -y && pkg upgrade -y

# 2. Install system dependencies
echo "[2/4] Installing Python, pip, and git..."
pkg install -y python python-pip git

# 3. Install claw-phone in editable mode
echo "[3/4] Installing claw-phone and Python dependencies..."
pip install --break-system-packages -e .

# 4. Create runtime directory
echo "[4/4] Creating ~/.claw-phone directory..."
mkdir -p "$HOME/.claw-phone/logs"

echo ""
echo "=== Installation complete ==="
echo ""
echo "Next steps:"
echo "  1. Run the setup wizard:  python -m claw_phone setup"
echo "  2. Start the gateway:     python -m claw_phone gateway"
echo "  3. Or use the watchdog:   bash scripts/watchdog.sh"
echo ""
