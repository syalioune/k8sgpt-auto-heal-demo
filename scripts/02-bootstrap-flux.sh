#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

source "$ROOT_DIR/.env"

GITHUB_BRANCH="${GITHUB_BRANCH:-main}"
OPENAI_MODEL="${OPENAI_MODEL:-gpt-4o-mini}"

echo "============================================="
echo " Step 2: Bootstrapping FluxCD (GitOps)"
echo "============================================="

# -----------------------------------------------------------------------
# 2a. Install Flux CLI if not present
# -----------------------------------------------------------------------
if ! command -v flux &>/dev/null; then
  echo "→ Installing Flux CLI..."
  curl -s https://fluxcd.io/install.sh | bash
fi

echo "→ Flux version:"
flux --version

# Pre-flight check
echo "→ Running Flux pre-flight checks..."
flux check --pre --timeout=5m

# -----------------------------------------------------------------------
# 2b. Render templates (envsubst for model name)
# -----------------------------------------------------------------------
echo "→ Rendering K8sGPT config template (model: ${OPENAI_MODEL})..."
export OPENAI_MODEL
envsubst '${OPENAI_MODEL}' \
  < "$ROOT_DIR/infrastructure/k8sgpt-config/k8sgpt-instance.yaml.tpl" \
  > "$ROOT_DIR/infrastructure/k8sgpt-config/k8sgpt-instance.yaml"

# -----------------------------------------------------------------------
# 2c. Commit rendered manifests so Flux can reconcile them
# -----------------------------------------------------------------------
echo "→ Committing infrastructure manifests..."
cd "$ROOT_DIR"
git add \
  clusters/ \
  infrastructure/ \
  apps/

if git diff --cached --quiet; then
  echo "  (no changes — manifests already committed)"
else
  git commit -m "feat: bootstrap GitOps infrastructure

- K8sGPT operator via HelmRelease (Flux-managed)
- K8sGPT config (OpenAI backend, ${OPENAI_MODEL})
- Auto-heal watcher deployment + RBAC
- Flux Kustomizations with dependency ordering:
  k8sgpt-operator → k8sgpt-config → watcher → apps
- Apps namespace (demo-apps)"
fi

git push origin "${GITHUB_BRANCH}"

# -----------------------------------------------------------------------
# 2d. Bootstrap Flux with GitHub (reentrant)
# -----------------------------------------------------------------------
echo "→ Bootstrapping Flux into cluster with GitHub repo '${GITHUB_USER}/${GITHUB_REPO}'..."
flux bootstrap github \
  --owner="${GITHUB_USER}" \
  --repository="${GITHUB_REPO}" \
  --branch="${GITHUB_BRANCH}" \
  --path=clusters/k8sgpt-demo \
  --personal \
  --token-auth \
  --timeout=10m

# Wait for Flux to be ready
echo "→ Waiting for Flux controllers to be ready..."
flux check --timeout=5m

echo "→ Flux components:"
kubectl -n flux-system get pods

echo ""
echo "✅ FluxCD bootstrapped with full GitOps infrastructure."
echo ""
echo "   Repo:         https://github.com/${GITHUB_USER}/${GITHUB_REPO}"
echo "   Cluster path: clusters/k8sgpt-demo"
echo ""
echo "   Flux will now reconcile the following in order:"
echo "     1. k8sgpt-operator   → HelmRelease installs the K8sGPT operator"
echo "     2. k8sgpt-config     → K8sGPT CR with OpenAI backend"
echo "     3. auto-heal-watcher → Watcher deployment + RBAC"
echo "     4. apps              → Demo apps (currently empty)"
echo ""
echo "   ⚠️  NEXT: Run step 03 to create secrets (API keys can't be stored in Git)."
