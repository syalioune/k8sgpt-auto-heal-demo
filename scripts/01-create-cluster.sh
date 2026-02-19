#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "============================================="
echo " Step 1: Creating Kind cluster"
echo "============================================="

# Check prerequisites
for cmd in kind kubectl helm; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: '$cmd' is required but not found."
    echo "Install instructions:"
    echo "  kind:    https://kind.sigs.k8s.io/docs/user/quick-start/#installation"
    echo "  kubectl: https://kubernetes.io/docs/tasks/tools/"
    echo "  helm:    https://helm.sh/docs/intro/install/"
    exit 1
  fi
done

# Delete existing cluster if present
if kind get clusters 2>/dev/null | grep -q "k8sgpt-demo"; then
  echo "→ Deleting existing 'k8sgpt-demo' cluster..."
  kind delete cluster --name k8sgpt-demo
fi

echo "→ Creating Kind cluster 'k8sgpt-demo' (1 control-plane + 2 workers)..."
kind create cluster --config "$ROOT_DIR/kind-config.yaml" --wait 120s

echo "→ Verifying cluster..."
kubectl cluster-info --context kind-k8sgpt-demo
kubectl get nodes

echo ""
echo "✅ Kind cluster 'k8sgpt-demo' is ready."
