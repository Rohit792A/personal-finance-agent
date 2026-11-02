#!/usr/bin/env bash
# Store a secret in Google Secret Manager without it ever appearing on screen, in your shell
# history, in a file, or in a chat. The value is typed at a hidden prompt.
#
# Usage:  scripts/set_secret.sh <secret-name>
#
# Secrets used by the app (see docs/OVERHAUL_PLAN.md, section 4):
#   pfa-db-password-personal   Supabase database password (personal environment)
#   pfa-db-password-demo       Supabase database password (demo environment, Phase 1)
#   pfa-gmail-app-password     16-character Gmail App Password (spaces are fine)
#   pfa-statement-passwords    Bank-statement PDF password(s), separated by commas
#
# Older versions are disabled (not destroyed), so only the new value can be read. To roll
# back:  gcloud secrets versions enable <version> --secret=<name>
# Restart the backend afterwards so it picks up the new value.
set -euo pipefail

usage() {
  sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
}

NAME="${1:-}"
if [[ -z "$NAME" || "$NAME" == "-h" || "$NAME" == "--help" ]]; then
  usage
  exit 1
fi
if [[ ! "$NAME" =~ ^[A-Za-z0-9_-]{1,255}$ ]]; then
  echo "Invalid secret name '$NAME' (letters, digits, '-' and '_' only)." >&2
  exit 1
fi

PROJECT="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
if [[ -z "$PROJECT" ]]; then
  echo "No Google Cloud project set. Run: gcloud config set project <project-id>" >&2
  exit 1
fi

read -r -s -p "Value for '$NAME' (input hidden): " VALUE; echo
read -r -s -p "Type it again to confirm: " VALUE_CONFIRM; echo
if [[ "$VALUE" != "$VALUE_CONFIRM" ]]; then
  echo "The two values didn't match. Nothing was stored." >&2
  exit 1
fi
unset VALUE_CONFIRM
if [[ -z "$VALUE" ]]; then
  echo "Empty value. Nothing was stored." >&2
  exit 1
fi

if ! gcloud secrets describe "$NAME" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud secrets create "$NAME" --project "$PROJECT" \
    --replication-policy=automatic --labels=app=personal-finance-agent >/dev/null
  echo "Created secret '$NAME'."
fi

# printf is a shell builtin, so the value never shows up in the process list.
NEW_VERSION="$(printf '%s' "$VALUE" | gcloud secrets versions add "$NAME" \
  --project "$PROJECT" --data-file=- --format='value(name)')"
unset VALUE
NEW_ID="${NEW_VERSION##*/}"
echo "Stored version $NEW_ID of '$NAME'."

for version in $(gcloud secrets versions list "$NAME" --project "$PROJECT" \
                   --filter='state=ENABLED' --format='value(name)'); do
  id="${version##*/}"
  if [[ "$id" != "$NEW_ID" ]]; then
    gcloud secrets versions disable "$id" --secret "$NAME" --project "$PROJECT" --quiet >/dev/null
    echo "Disabled old version $id."
  fi
done

echo "Done. Restart the backend so it uses the new value."
