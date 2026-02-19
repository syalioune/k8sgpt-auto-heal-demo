#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  k8sgpt-auto-heal — GitOps AI Auto-Healing Demo          ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

# Check .env exists
if [ ! -f "$ROOT_DIR/.env" ]; then
  echo "ERROR: .env file not found."
  echo "  cp .env.example .env"
  echo "  # Then fill in your GITHUB_TOKEN, OPENAI_API_KEY, etc."
  exit 1
fi

source "$ROOT_DIR/.env"

# Validate required vars
for var in GITHUB_TOKEN GITHUB_USER GITHUB_REPO OPENAI_API_KEY; do
  if [ -z "${!var:-}" ]; then
    echo "ERROR: ${var} is not set in .env"
    exit 1
  fi
done

# Make scripts executable
chmod +x "$ROOT_DIR/scripts/"*.sh

ACTION="${1:-full}"

case "$ACTION" in
  full)
    echo "Running full GitOps setup..."
    echo ""
    "$ROOT_DIR/scripts/01-create-cluster.sh"
    echo ""
    "$ROOT_DIR/scripts/02-bootstrap-flux.sh"
    echo ""
    "$ROOT_DIR/scripts/03-create-secrets.sh"
    echo ""

    echo "============================================="
    echo " GitOps Setup complete!"
    echo "============================================="
    echo ""
    echo " Everything is managed by Flux. Next steps:"
    echo ""
    echo " 1. Deploy the watcher (choose one):"
    echo "    ./setup.sh watcher cluster   # In-cluster via Flux (recommended)"
    echo "    ./setup.sh watcher local     # Local dev mode"
    echo ""
    echo " 2. Deploy broken apps (pushed to Git, Flux reconciles):"
    echo "    ./setup.sh break all"
    echo ""
    echo " 3. Watch the pipeline:"
    echo "    flux get kustomizations"
    echo "    kubectl get results -n k8sgpt-operator-system -w"
    echo "    # Check GitHub for auto-generated PRs"
    echo ""
    echo " 4. Approve and merge a PR → Flux reconciles → pod heals!"
    echo ""
    ;;

  cluster)
    echo "Running Steps 1-3 only (cluster + flux + secrets)..."
    "$ROOT_DIR/scripts/01-create-cluster.sh"
    "$ROOT_DIR/scripts/02-bootstrap-flux.sh"
    "$ROOT_DIR/scripts/03-create-secrets.sh"
    ;;

  watcher)
    "$ROOT_DIR/scripts/04-deploy-watcher.sh" "${2:-cluster}"
    ;;

  break)
    "$ROOT_DIR/scripts/05-deploy-broken-apps.sh" "${2:-all}"
    ;;

  teardown)
    "$ROOT_DIR/scripts/06-teardown.sh"
    ;;

  *)
    echo "Usage: $0 [full|cluster|watcher|break|teardown]"
    echo ""
    echo "  full      - Complete GitOps setup (cluster + flux + secrets)"
    echo "  cluster   - Create cluster + bootstrap flux + create secrets only"
    echo "  watcher   - Deploy the auto-heal watcher (add 'cluster' or 'local')"
    echo "  break     - Push broken apps to fleet repo (add 'all|nginx|service|oom|image')"
    echo "  teardown  - Destroy everything"
    exit 1
    ;;
esac
