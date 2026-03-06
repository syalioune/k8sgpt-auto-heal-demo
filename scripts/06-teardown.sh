#!/usr/bin/env bash
set -euo pipefail

echo "============================================="
echo " Teardown: Destroying demo environment"
echo "============================================="

echo "→ Deleting Kind cluster 'k8sgpt-demo'..."
kind delete cluster --name k8sgpt-demo 2>/dev/null || true

echo "→ Cleaning up temp files..."
rm -rf /tmp/k8sgpt-context.json
rm -rf /tmp/apps-kustomization.yaml

echo ""
echo "✅ Demo environment destroyed."
echo ""
echo "⚠️  The GitHub repo was NOT deleted. Clean up manually if needed:"
echo "   https://github.com/settings/repositories"
