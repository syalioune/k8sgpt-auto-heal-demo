# K8sGPT instance — deployed after the operator is ready.
# The OPENAI_MODEL placeholder is replaced by envsubst during setup.
apiVersion: core.k8sgpt.ai/v1alpha1
kind: K8sGPT
metadata:
  name: k8sgpt-openai
  namespace: k8sgpt-operator-system
spec:
  ai:
    enabled: true
    backend: openai
    model: ${OPENAI_MODEL}
    secret:
      name: k8sgpt-openai-secret
      key: openai-api-key
    anonymized: false
  noCache: false
  version: v0.4.26
  repository: ghcr.io/k8sgpt-ai/k8sgpt
  # Filter which resource types K8sGPT should analyze
  filters:
    - Pod
    - Service
    - Deployment
    - ReplicaSet
    - PersistentVolumeClaim
    - Ingress
    - StatefulSet
    - CronJob
    - Node
  # Scan every 2 minutes for the demo (production: 5-10m)
  analysis:
    interval: 2m