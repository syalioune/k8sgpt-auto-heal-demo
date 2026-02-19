#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

source "$ROOT_DIR/.env"

GITHUB_BRANCH="${GITHUB_BRANCH:-main}"

SCENARIO="${1:-all}"  # "all", "nginx", "service", "oom", "image"

echo "============================================="
echo " Step 5: Deploying Broken Apps via GitOps"
echo "         (scenario: ${SCENARIO})"
echo "============================================="

echo ""
echo "  Manifests are committed to the repo."
echo "  Flux reconciles them into the cluster."
echo "  NO direct kubectl apply — pure GitOps."
echo ""

deploy_app() {
  local file="$1"
  local name
  name=$(basename "$file" .yaml)

  echo "→ Adding ${name} to apps/k8sgpt-demo/..."
  mkdir -p "$ROOT_DIR/apps/k8sgpt-demo"
  cp "$file" "$ROOT_DIR/apps/k8sgpt-demo/${name}.yaml"
}

BROKEN_DIR="$ROOT_DIR/manifests/broken-apps"

case "$SCENARIO" in
  all)
    for f in "$BROKEN_DIR"/*.yaml; do
      deploy_app "$f"
    done
    ;;
  nginx)
    deploy_app "$BROKEN_DIR/nginx-readonly.yaml"
    ;;
  service)
    deploy_app "$BROKEN_DIR/service-no-endpoints.yaml"
    ;;
  oom)
    deploy_app "$BROKEN_DIR/memory-hog.yaml"
    ;;
  image)
    deploy_app "$BROKEN_DIR/bad-image.yaml"
    ;;
  *)
    echo "Usage: $0 [all|nginx|service|oom|image]"
    exit 1
    ;;
esac

# Commit and push
cd "$ROOT_DIR"
git add apps/
if git diff --cached --quiet; then
  echo "  (no changes — apps already committed)"
else
  git commit -m "deploy broken app(s): ${SCENARIO} [auto-heal demo]"
  git push origin "${GITHUB_BRANCH}"
  echo "  ✅ Pushed — Flux will reconcile shortly"
fi

# Trigger Flux reconciliation immediately instead of waiting
echo ""
echo "→ Triggering Flux reconciliation..."
flux reconcile source git flux-system 2>/dev/null || true
sleep 2
flux reconcile kustomization apps 2>/dev/null || true

echo ""
echo "→ Waiting for Flux to deploy apps..."
sleep 10

echo "→ Pod status in demo-apps namespace:"
kubectl get pods -n demo-apps 2>/dev/null || echo "  (namespace may not exist yet — Flux is reconciling)"

echo ""
echo "✅ Broken apps committed and pushed."
echo "   Flux will deploy them within ~5 minutes (or sooner via reconcile trigger)."
echo ""
echo "   Watch Flux status:      flux get kustomizations"
echo "   Watch K8sGPT results:   kubectl get results -n k8sgpt-operator-system -w"
echo "   Watch pod status:       kubectl get pods -n demo-apps -w"
