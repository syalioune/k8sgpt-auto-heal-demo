#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

source "$ROOT_DIR/.env"

GITHUB_BRANCH="${GITHUB_BRANCH:-main}"
ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-claude-sonnet-4-20250514}"

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
flux check --pre

# -----------------------------------------------------------------------
# 2b. Bootstrap Flux with GitHub
# -----------------------------------------------------------------------
echo "→ Bootstrapping Flux into cluster with GitHub repo '${GITHUB_USER}/${GITHUB_REPO}'..."
flux bootstrap github \
  --owner="${GITHUB_USER}" \
  --repository="${GITHUB_REPO}" \
  --branch="${GITHUB_BRANCH}" \
  --path=clusters/k8sgpt-demo \
  --personal \
  --token-auth

# Wait for Flux to be ready
echo "→ Waiting for Flux controllers to be ready..."
flux check

echo "→ Flux components:"
kubectl -n flux-system get pods

# -----------------------------------------------------------------------
# 2c. Push all Flux manifests to the fleet repo
# -----------------------------------------------------------------------
echo ""
echo "→ Pushing infrastructure manifests to fleet repo..."

REPO_DIR="/tmp/${GITHUB_REPO}"

# Clone (or pull) the fleet repo
if [ -d "$REPO_DIR" ]; then
  cd "$REPO_DIR"
  git pull --rebase origin "${GITHUB_BRANCH}" || true
else
  git clone "https://${GITHUB_TOKEN}@github.com/${GITHUB_USER}/${GITHUB_REPO}.git" "$REPO_DIR"
  cd "$REPO_DIR"
fi

# --- Cluster-level Flux Kustomizations (reconciliation graph) ---
echo "→ Copying Flux Kustomization resources..."
cp "$ROOT_DIR/flux/clusters/k8sgpt-operator.yaml" clusters/k8sgpt-demo/
cp "$ROOT_DIR/flux/clusters/k8sgpt-config.yaml"   clusters/k8sgpt-demo/
cp "$ROOT_DIR/flux/clusters/watcher.yaml"          clusters/k8sgpt-demo/
cp "$ROOT_DIR/flux/clusters/apps.yaml"             clusters/k8sgpt-demo/

# --- K8sGPT Operator infrastructure (HelmRepo + HelmRelease) ---
echo "→ Copying K8sGPT operator manifests..."
mkdir -p infrastructure/k8sgpt-operator
cp "$ROOT_DIR/flux/infrastructure/k8sgpt-operator/kustomization.yaml"  infrastructure/k8sgpt-operator/
cp "$ROOT_DIR/flux/infrastructure/k8sgpt-operator/namespace.yaml"      infrastructure/k8sgpt-operator/
cp "$ROOT_DIR/flux/infrastructure/k8sgpt-operator/helmrepository.yaml" infrastructure/k8sgpt-operator/
cp "$ROOT_DIR/flux/infrastructure/k8sgpt-operator/helmrelease.yaml"    infrastructure/k8sgpt-operator/

# --- K8sGPT Config (K8sGPT CR instance) — requires envsubst for model name ---
echo "→ Copying K8sGPT config manifests (model: ${ANTHROPIC_MODEL})..."
mkdir -p infrastructure/k8sgpt-config
cp "$ROOT_DIR/flux/infrastructure/k8sgpt-config/kustomization.yaml" infrastructure/k8sgpt-config/
export ANTHROPIC_MODEL
envsubst '${ANTHROPIC_MODEL}' \
  < "$ROOT_DIR/flux/infrastructure/k8sgpt-config/k8sgpt-instance.yaml" \
  > infrastructure/k8sgpt-config/k8sgpt-instance.yaml

# --- Watcher infrastructure (RBAC + Deployment) ---
echo "→ Copying watcher manifests..."
mkdir -p infrastructure/watcher
cp "$ROOT_DIR/flux/infrastructure/watcher/kustomization.yaml" infrastructure/watcher/
cp "$ROOT_DIR/watcher/k8s-manifests/rbac.yaml"                infrastructure/watcher/
cp "$ROOT_DIR/watcher/k8s-manifests/deployment.yaml"           infrastructure/watcher/

# --- Apps directory (namespace + placeholder) ---
echo "→ Initializing apps directory..."
mkdir -p apps/k8sgpt-demo
cp "$ROOT_DIR/flux/apps/k8sgpt-demo/namespace.yaml" apps/k8sgpt-demo/

# --- Commit and push ---
git add .

if git diff --cached --quiet; then
  echo "  (no changes to push — fleet repo is up to date)"
else
  git commit -m "feat: bootstrap GitOps infrastructure

- K8sGPT operator via HelmRelease (Flux-managed)
- K8sGPT config (Anthropic backend, ${ANTHROPIC_MODEL})
- Auto-heal watcher deployment + RBAC
- Flux Kustomizations with dependency ordering:
  k8sgpt-operator → k8sgpt-config → watcher → apps
- Apps namespace (demo-apps)"

  git push origin "${GITHUB_BRANCH}"
  echo "  ✅ Pushed all infrastructure manifests to fleet repo"
fi

cd "$ROOT_DIR"

echo ""
echo "✅ FluxCD bootstrapped with full GitOps infrastructure."
echo ""
echo "   Fleet repo:   https://github.com/${GITHUB_USER}/${GITHUB_REPO}"
echo "   Cluster path: clusters/k8sgpt-demo"
echo ""
echo "   Flux will now reconcile the following in order:"
echo "     1. k8sgpt-operator   → HelmRelease installs the K8sGPT operator"
echo "     2. k8sgpt-config     → K8sGPT CR with Anthropic backend"
echo "     3. auto-heal-watcher → Watcher deployment + RBAC"
echo "     4. apps              → Demo apps (currently empty)"
echo ""
echo "   ⚠️  NEXT: Run step 03 to create secrets (API keys can't be stored in Git)."
