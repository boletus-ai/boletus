#!/usr/bin/env bash
# Crewmatic — one-command install and setup
set -e

echo "=== Installing Crewmatic ==="
echo

# Create venv if needed
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate

echo "Installing dependencies..."
pip install -q -e .

echo
echo "Done! Starting setup..."
echo

crewmatic init
