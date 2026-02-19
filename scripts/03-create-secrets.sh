#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

source "$ROOT_DIR/.env"

GITHUB_BRANCH="${GITHUB_BRANCH:-main}"
ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-claude-sonnet-4-20250514}"
REMEDIATION_MODE="${REMEDIATION_MODE:-api}"

echo "============================================="
echo " Step 3: Creating Secrets"
echo "============================================="

echo ""
echo "  Secrets contain API keys and cannot be stored in Git."
echo "  This is the only step that uses kubectl directly."
echo ""

K8SGPT_NS="k8sgpt-operator-system"
WATCHER_NS="k8sgpt-auto-heal"

# -----------------------------------------------------------------------
# 3a. Wait for Flux to create the namespaces
# -----------------------------------------------------------------------
echo "→ Waiting for Flux to create namespaces..."

for ns in "$K8SGPT_NS" "$WATCHER_NS"; do
  echo "  Waiting for namespace '$ns'..."
  for i in $(seq 1 30); do
    if kubectl get namespace "$ns" &>/dev/null; then
      echo "  ✓ Namespace '$ns' exists"
      break
    fi
    if [ "$i" -eq 30 ]; then
      echo "  ⚠ Namespace '$ns' not yet created by Flux — creating it manually..."
      kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
    fi
    sleep 5
  done
done

# -----------------------------------------------------------------------
# 3b. K8sGPT Anthropic API secret
# -----------------------------------------------------------------------
echo "→ Creating Anthropic API key secret for K8sGPT..."
kubectl -n "$K8SGPT_NS" create secret generic k8sgpt-anthropic-secret \
  --from-literal=anthropic-api-key="${ANTHROPIC_API_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

# -----------------------------------------------------------------------
# 3c. Auto-heal watcher secrets
# -----------------------------------------------------------------------
echo "→ Creating auto-heal watcher secrets..."
kubectl -n "$WATCHER_NS" create secret generic auto-heal-secrets \
  --from-literal=GITHUB_TOKEN="${GITHUB_TOKEN}" \
  --from-literal=GITHUB_REPO="${GITHUB_USER}/${GITHUB_REPO}" \
  --from-literal=GITHUB_BRANCH="${GITHUB_BRANCH}" \
  --from-literal=ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
  --from-literal=ANTHROPIC_MODEL="${ANTHROPIC_MODEL}" \
  --dry-run=client -o yaml | kubectl apply -f -

# -----------------------------------------------------------------------
# 3d. Trigger Flux reconciliation so it picks up the secrets
# -----------------------------------------------------------------------
echo "→ Triggering Flux reconciliation..."
flux reconcile source git flux-system 2>/dev/null || true
sleep 2
flux reconcile kustomization k8sgpt-operator 2>/dev/null || true
flux reconcile kustomization k8sgpt-config 2>/dev/null || true
flux reconcile kustomization auto-heal-watcher 2>/dev/null || true

echo ""
echo "→ Waiting for K8sGPT operator to be ready..."
kubectl -n "$K8SGPT_NS" wait --for=condition=available deployment \
  --all --timeout=300s || echo "  ⚠ Timed out — Flux may still be reconciling"

echo ""
echo "→ K8sGPT Operator pods:"
kubectl -n "$K8SGPT_NS" get pods 2>/dev/null || true

echo ""
echo "✅ Secrets created. Flux is reconciling infrastructure."
echo ""
echo "   Monitor Flux progress:  flux get kustomizations"
echo "   K8sGPT results:        kubectl get results -n ${K8SGPT_NS} -w"
echo "   Watcher status:        kubectl -n ${WATCHER_NS} get pods"
