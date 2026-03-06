#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

source "$ROOT_DIR/.env"

GITHUB_BRANCH="${GITHUB_BRANCH:-main}"
OPENAI_MODEL="${OPENAI_MODEL:-gpt-4o-mini}"

MODE="${1:-cluster}"  # "local" or "cluster"

echo "============================================="
echo " Step 4: Deploy Auto-Heal Watcher (${MODE})"
echo "============================================="

K8SGPT_NS="k8sgpt-operator-system"

if [ "$MODE" = "local" ]; then
  # -----------------------------------------------------------------------
  # LOCAL MODE: run the watcher directly on your machine (dev/debug)
  # -----------------------------------------------------------------------
  echo "→ Running watcher locally (uses your kubeconfig)..."
  echo ""
  echo "  NOTE: In local mode the watcher runs outside the cluster."
  echo "  The in-cluster watcher deployment (managed by Flux) will"
  echo "  be suspended to avoid duplicate processing."
  echo ""

  # Suspend the in-cluster watcher Flux kustomization
  flux suspend kustomization auto-heal-watcher --timeout=2m 2>/dev/null || true

  # Install Python deps
  echo "→ Installing Python dependencies..."
  pip install -r "$ROOT_DIR/watcher/requirements.txt" --quiet

  echo "→ Starting watcher (Ctrl+C to stop)..."
  echo ""

  export K8SGPT_NAMESPACE="$K8SGPT_NS"
  export GITHUB_REPO="${GITHUB_USER}/${GITHUB_REPO}"
  export GITHUB_BRANCH="${GITHUB_BRANCH}"
  export FLEET_APPS_PATH="apps/k8sgpt-demo"
  export OPENAI_API_KEY="${OPENAI_API_KEY}"
  export OPENAI_MODEL="${OPENAI_MODEL}"
  export GITHUB_TOKEN="${GITHUB_TOKEN}"
  export LOG_LEVEL="INFO"
  export DRY_RUN="${DRY_RUN:-false}"

  python3 "$ROOT_DIR/watcher/watcher.py"

elif [ "$MODE" = "cluster" ]; then
  # -----------------------------------------------------------------------
  # CLUSTER MODE: build container, load into Kind, Flux handles deployment
  # -----------------------------------------------------------------------

  # Resume the Flux kustomization in case it was suspended
  flux resume kustomization auto-heal-watcher --timeout=2m 2>/dev/null || true

  echo "→ Building watcher container image..."
  docker build -t auto-heal-watcher:local "$ROOT_DIR/watcher"

  echo "→ Loading image into Kind cluster..."
  kind load docker-image auto-heal-watcher:local --name k8sgpt-demo

  echo "→ Triggering Flux reconciliation for watcher..."
  flux reconcile kustomization auto-heal-watcher --timeout=1m 2>/dev/null || true

  echo "→ Waiting for watcher deployment to be ready..."
  kubectl -n k8sgpt-auto-heal wait --for=condition=available deployment/auto-heal-watcher \
    --timeout=120s || echo "  ⚠ Timed out — check: kubectl -n k8sgpt-auto-heal get pods"

  echo ""
  echo "→ Watcher pod:"
  kubectl -n k8sgpt-auto-heal get pods

  echo ""
  echo "✅ Watcher deployed in-cluster (via Flux)."
  echo "   Logs: kubectl -n k8sgpt-auto-heal logs -f deployment/auto-heal-watcher"

else
  echo "Usage: $0 [local|cluster]"
  exit 1
fi
