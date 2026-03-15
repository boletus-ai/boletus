#!/usr/bin/env bash
# Boletus — one-command install and setup
set -e

echo "=== Installing Boletus ==="
echo

# Create venv if needed
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate

echo "Installing dependencies..."
pip install -q -e .

# Collect Slack tokens if .env doesn't exist
if [ ! -f ".env" ]; then
    echo
    echo "=== Slack Setup ==="
    echo
    echo "1. Go to https://api.slack.com/apps"
    echo "2. Click 'Create New App' > 'From a manifest'"
    echo "3. Select your workspace"
    echo "4. Paste the contents of slack-app-manifest.json (in this directory)"
    echo "5. Click 'Create'"
    echo

    echo "Now get your tokens:"
    echo
    echo "  App Token: Basic Information > scroll to 'App-Level Tokens'"
    echo "  > Generate Token and Scopes > add 'connections:write' > Generate"
    read -sp "  Paste App Token (xapp-...): " APP_TOKEN
    echo

    echo "  Bot Token: Install App (left menu) > Install to Workspace > copy Bot Token"
    read -sp "  Paste Bot Token (xoxb-...): " BOT_TOKEN
    echo

    echo "  Your Slack User ID: click your profile > three dots > Copy member ID"
    read -p "  Paste Member ID (U...): " OWNER_ID
    echo

    cat > .env << EOF
SLACK_APP_TOKEN="${APP_TOKEN}"
SLACK_BOT_TOKEN="${BOT_TOKEN}"
OWNER_SLACK_ID="${OWNER_ID}"
EOF
    echo "Saved tokens to .env"
else
    echo
    echo "Found existing .env — skipping token setup."
fi

echo
echo "=== Ready! ==="
echo
echo "  source .venv/bin/activate"
echo "  boletus setup       # wizard creates team + channels in Slack"
echo
