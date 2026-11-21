#!/usr/bin/env bash
# First-time setup (and later reconfiguration): pick where processing runs, download its models, start, create the account.
#   ./setup.sh             ask only what isn't configured yet
#   ./setup.sh --reconfigure   ask the compute mode again
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example."; }

get() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- || true; }
put() {
  if grep -qE "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else printf '\n%s=%s\n' "$1" "$2" >> .env; fi
}

mode=$(get COMPUTE_MODE)
if [ -z "$mode" ] || [ "${1:-}" = "--reconfigure" ]; then
  cat <<'TXT'

Where should Saved process your reels (speech, on-screen text, tagging, search vectors)?

  1) Server only   Everything runs on this machine's CPU. No PC needed, but slow:
                   several minutes per reel. Downloads about 6 GB of models here.
  2) Hybrid        A Windows PC with an NVIDIA GPU does all processing, about 10-20 s
                   per reel. This server only downloads, stores and searches. Downloads
                   about 1.5 GB of search models here; the PC downloads the rest.
                   Items wait while the PC is off.

TXT
  while :; do
    read -rp "Choose 1 or 2 [${mode:-none}]: " ans
    case "${ans:-}" in
      1) mode=server; break ;;
      2) mode=hybrid; break ;;
      "") [ -n "$mode" ] && break ;;
    esac
  done
  put COMPUTE_MODE "$mode"
  echo "COMPUTE_MODE=$mode saved in .env."
fi

mkdir -p data/models data/secrets
docker compose up -d --build

echo
echo "Downloading models for $mode mode (safe to re-run; finished downloads are skipped)…"
docker compose exec -T api python -m app.models_setup "$mode"

if ! docker compose exec -T api python -c "from app import auth, db; import sys; sys.exit(0 if auth.has_account(db.open_db()) else 1)"; then
  echo
  echo "Create your sign-in account:"
  docker compose exec api python -m app.auth set-password
fi

ip=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "Saved is running at http://${ip:-<server-ip>}:8080/"
if [ "$mode" = hybrid ]; then
  echo "Next: on the PC, start processing with tools/pc-compute (or the desktop app's PC compute card)."
fi
