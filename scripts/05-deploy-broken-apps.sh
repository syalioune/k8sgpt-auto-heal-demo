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
echo "  Manifests are pushed to the fleet repo."
echo "  Flux reconciles them into the cluster."
echo "  NO direct kubectl apply — pure GitOps."
echo ""

# Use an isolated temp dir per run — avoids stale state from previous runs
REPO_DIR=$(mktemp -d "/tmp/fleet-${GITHUB_REPO}-XXXXXX")
trap 'rm -rf "$REPO_DIR"' EXIT

git clone --branch "${GITHUB_BRANCH}" --single-branch \
  "https://${GITHUB_TOKEN}@github.com/${GITHUB_USER}/${GITHUB_REPO}.git" "$REPO_DIR"

push_to_fleet() {
  local file="$1"
  local name
  name=$(basename "$file" .yaml)

  echo "→ Pushing ${name} to fleet repo (apps/k8sgpt-demo/)..."

  cd "$REPO_DIR"
  git pull --rebase origin "${GITHUB_BRANCH}" || true

  mkdir -p "apps/k8sgpt-demo"
  cp "$file" "apps/k8sgpt-demo/${name}.yaml"
  git add .

  if git diff --cached --quiet; then
    echo "  (no changes — ${name} already in fleet repo)"
  else
    git commit -m "deploy broken app: ${name} [auto-heal demo]"
    git push origin "${GITHUB_BRANCH}"
    echo "  ✅ Pushed to fleet repo — Flux will reconcile shortly"
  fi

  cd "$ROOT_DIR"
}

BROKEN_DIR="$ROOT_DIR/manifests/broken-apps"

case "$SCENARIO" in
  all)
    for f in "$BROKEN_DIR"/*.yaml; do
      push_to_fleet "$f"
    done
    ;;
  nginx)
    push_to_fleet "$BROKEN_DIR/nginx-readonly.yaml"
    ;;
  service)
    push_to_fleet "$BROKEN_DIR/service-no-endpoints.yaml"
    ;;
  oom)
    push_to_fleet "$BROKEN_DIR/memory-hog.yaml"
    ;;
  image)
    push_to_fleet "$BROKEN_DIR/bad-image.yaml"
    ;;
  *)
    echo "Usage: $0 [all|nginx|service|oom|image]"
    exit 1
    ;;
esac

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
echo "✅ Broken apps pushed to fleet repo."
echo "   Flux will deploy them within ~5 minutes (or sooner via reconcile trigger)."
echo ""
echo "   Watch Flux status:      flux get kustomizations"
echo "   Watch K8sGPT results:   kubectl get results -n k8sgpt-operator-system -w"
echo "   Watch pod status:       kubectl get pods -n demo-apps -w"
