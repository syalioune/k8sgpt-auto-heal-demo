# k8sgpt-auto-heal

**GitOps-powered AI auto-healing for Kubernetes using K8sGPT + OpenAI + FluxCD**

This demo sets up a local Kind cluster with a complete auto-remediation pipeline:
K8sGPT detects issues → OpenAI generates fixes → PRs are created on GitHub → Flux reconciles after human approval.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      Kind Cluster                                │
│                                                                  │
│  ┌────────────┐                                                 │
│  │  FluxCD    │ ◀── watches ── GitHub Repo (this repo)         │
│  │            │                                                  │
│  │ Reconciles │──┬─ HelmRelease ──▶ K8sGPT Operator             │
│  │ everything │  ├─ Kustomization ─▶ K8sGPT Config (CR)         │
│  │ from Git   │  ├─ Kustomization ─▶ Auto-Heal Watcher          │
│  │            │  └─ Kustomization ─▶ Demo Apps                  │
│  └────────────┘                                                  │
│                                                                  │
│  ┌────────────┐  Result CRD   ┌─────────────────┐              │
│  │  K8sGPT    │──────────────▶│  auto-heal      │              │
│  │  Operator   │               │  watcher        │              │
│  │             │               │  (Python)       │              │
│  │ • scans     │               │                 │              │
│  │   every 2m  │               │ • watches       │              │
│  │ • OpenAI   │               │   Result CRDs   │              │
│  │   backend   │               │ • gathers k8s   │              │
│  └────────────┘               │   context       │              │
│        ▲                       │ • calls OpenAI  │              │
│        │                       │   for fix       │              │
│  ┌─────┴──────┐               │ • creates PR    │              │
│  │ Broken     │               └────────┬────────┘              │
│  │ Apps       │                        │                        │
│  │ (via Flux) │                        │ GitHub API             │
│  └────────────┘                        ▼                        │
│                                ┌───────────────┐               │
│                                │  GitHub Repo   │               │
│                                │  (this repo)   │               │
│                                │                │               │
│                                │ clusters/      │               │
│                                │ infrastructure/│               │
│                                │ apps/          │               │
│                                └───────────────┘               │
└────────────────────────────────────────────────────────────────┘

Flux Reconciliation Graph (dependency order):
  k8sgpt-operator → k8sgpt-config → auto-heal-watcher → apps
```

## End-to-End Flow

### Infrastructure Provisioning (GitOps)

1. **Flux bootstrap** connects the cluster to this GitHub repo
2. **All manifests** (K8sGPT operator, watcher, apps) are committed in Git
3. **Flux reconciles** in dependency order:
   - `k8sgpt-operator` → HelmRelease installs the K8sGPT operator
   - `k8sgpt-config` → K8sGPT CR configures OpenAI backend
   - `auto-heal-watcher` → Watcher deployment + RBAC
   - `apps` → Demo apps namespace + broken workloads
4. **Secrets** (API keys) are the only resources created via `kubectl`

### Auto-Remediation Loop

1. **Broken app is deployed** via Flux from the repo
2. **K8sGPT Operator scans** the cluster every 2 minutes
3. K8sGPT detects an issue and creates a **`Result` CRD** with:
   - Error details (CrashLoopBackOff, no endpoints, OOMKilled, etc.)
   - AI explanation from OpenAI
4. **Auto-heal watcher** detects the new `Result` CRD
5. Watcher **gathers full context**: pod spec, logs, events, deployment YAML
6. Watcher **calls OpenAI** with context + instructions
7. OpenAI generates a **fixed manifest** + **PR description**
8. Watcher **creates a GitHub PR** on the repo with the fix
9. **Human reviews and approves** the PR (safety gate)
10. PR is merged → **Flux reconciles** → broken app is healed

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Docker | 20+ | https://docs.docker.com/get-docker/ |
| Kind | 0.20+ | `go install sigs.k8s.io/kind@latest` |
| kubectl | 1.28+ | https://kubernetes.io/docs/tasks/tools/ |
| Helm | 3.12+ | https://helm.sh/docs/intro/install/ |
| Flux CLI | 2.x | `curl -s https://fluxcd.io/install.sh \| bash` |
| Python | 3.10+ | https://www.python.org/ |
| Git | 2.x | (usually pre-installed) |

**API Keys needed:**
- **GitHub Personal Access Token** with `repo` scope
- **OpenAI API Key** from https://platform.openai.com/api-keys

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/syalioune/k8sgpt-auto-heal-demo
cd k8sgpt-auto-heal-demo

cp .env.example .env
# Edit .env with your tokens:
#   GITHUB_TOKEN=ghp_...
#   GITHUB_USER=your-username
#   GITHUB_REPO=k8sgpt-fleet-demo
#   OPENAI_API_KEY=sk-...
```

### 2. Full setup (cluster + Flux + K8sGPT)

```bash
chmod +x setup.sh scripts/*.sh
./setup.sh full
```

This creates:
- A 3-node Kind cluster (`k8sgpt-demo`)
- FluxCD bootstrapped with your GitHub repo
- All infrastructure manifests pushed to Git (K8sGPT, watcher, apps)
- Flux reconciles everything in dependency order
- Secrets created for API keys

### 3. Deploy the watcher

```bash
# In-cluster mode (recommended — Flux manages the deployment)
./setup.sh watcher cluster

# OR local mode (dev/debug — runs Python directly)
./setup.sh watcher local
```

### 4. Deploy broken apps (pushed to Git, Flux reconciles)

```bash
# Deploy all broken scenarios
./setup.sh break all

# Or deploy individually
./setup.sh break nginx    # CrashLoopBackOff (read-only filesystem)
./setup.sh break oom      # OOMKilled (memory too low)
./setup.sh break service  # Service with no endpoints
./setup.sh break image    # ImagePullBackOff (wrong image tag)
```

### 5. Watch the pipeline

```bash
# Terminal 2: watch Flux reconciliation status
flux get kustomizations

# Terminal 3: watch K8sGPT results appear
kubectl get results -n k8sgpt-operator-system -w

# Terminal 4: watch pod status
kubectl get pods -n demo-apps -w

# Check GitHub for auto-generated PRs!
```

### 6. Approve → Flux heals

1. Go to your GitHub repo → Pull Requests
2. Review the auto-generated PR (has full description of problem + fix)
3. Merge the PR
4. Flux detects the change and reconciles within ~5 minutes
5. The broken pod is replaced with the fixed version

## Demo Scenarios

| Scenario | What's broken | What K8sGPT detects | What OpenAI fixes |
|----------|--------------|---------------------|-------------------|
| `nginx` | `readOnlyRootFilesystem: true` | CrashLoopBackOff | Adds `emptyDir` volumes for `/var/cache/nginx` and `/var/run` |
| `oom` | Memory limit 100Mi, app uses 200Mi | OOMKilled | Increases memory limit to 256Mi+ |
| `service` | Selector matches no pods | Service has no endpoints | Fixes selector or flags the mismatch |
| `image` | Non-existent image tag | ImagePullBackOff | Corrects image to a valid tag |

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GITHUB_TOKEN` | GitHub PAT with `repo` scope | (required) |
| `GITHUB_USER` | GitHub username/org | (required) |
| `GITHUB_REPO` | GitHub repo name | (required) |
| `GITHUB_BRANCH` | Target branch | `main` |
| `OPENAI_API_KEY` | OpenAI API key | (required) |
| `OPENAI_MODEL` | OpenAI model to use | `gpt-4o-mini` |
| `DRY_RUN` | Set to `true` to skip PR creation | `false` |
| `LOG_LEVEL` | Python log level | `INFO` |

## Project Structure

```
k8sgpt-auto-heal/
├── setup.sh                     # Main entry point
├── .env.example                 # Config template
├── kind-config.yaml             # Kind cluster definition
│
├── scripts/
│   ├── 01-create-cluster.sh     # Create Kind cluster
│   ├── 02-bootstrap-flux.sh     # Render templates + bootstrap FluxCD
│   ├── 03-create-secrets.sh     # Create K8s secrets (API keys)
│   ├── 04-deploy-watcher.sh     # Build image / run watcher
│   ├── 05-deploy-broken-apps.sh # Commit broken apps to Git (Flux deploys)
│   ├── 06-teardown.sh           # Destroy everything

│
├── clusters/
│   └── k8sgpt-demo/             # Flux Kustomization resources
│       ├── k8sgpt-operator.yaml # → infrastructure/k8sgpt-operator/
│       ├── k8sgpt-config.yaml   # → infrastructure/k8sgpt-config/
│       ├── watcher.yaml         # → infrastructure/watcher/
│       └── apps.yaml            # → apps/k8sgpt-demo/
│
├── infrastructure/
│   ├── k8sgpt-operator/         # HelmRepository + HelmRelease
│   ├── k8sgpt-config/           # K8sGPT CR (OpenAI backend)
│   │   ├── k8sgpt-instance.yaml.tpl  # Template (${OPENAI_MODEL})
│   │   └── kustomization.yaml
│   └── watcher/                 # Watcher deployment + RBAC
│       ├── rbac.yaml
│       ├── deployment.yaml
│       └── kustomization.yaml
│
├── apps/
│   └── k8sgpt-demo/
│       └── namespace.yaml       # demo-apps namespace
│
├── manifests/
│   └── broken-apps/             # Intentionally broken K8s manifests
│       ├── nginx-readonly.yaml  # CrashLoopBackOff scenario
│       ├── memory-hog.yaml      # OOMKilled scenario
│       ├── service-no-endpoints.yaml
│       └── bad-image.yaml       # ImagePullBackOff scenario
│
└── watcher/
    ├── watcher.py               # Main watch loop & entry point
    ├── config.py                # Environment variables & logging
    ├── k8s_helpers.py           # Kubernetes client & context gathering
    ├── remediation.py           # OpenAI prompt, API call, response parsing
    ├── github_pr.py             # GitHub PR creation
    ├── requirements.txt         # Python dependencies
    └── Dockerfile               # Container image
```

## How the Watcher Works

The watcher is a Python controller that implements a standard Kubernetes watch loop:

```python
# Simplified flow
while True:
    for event in watch(Result CRDs):
        if event is NEW or MODIFIED:
            # 1. Skip if already processed (check annotation)
            # 2. Gather context (pod spec, logs, events, deployment)
            # 3. Call OpenAI with context → get fix manifest + PR description
            # 4. Create GitHub PR with the fix
            # 5. Annotate Result CRD as "pr-created"
```

**Annotations used for state tracking:**
- `k8sgpt-auto-heal/remediation-state`: `in-progress` | `pr-created` | `failed` | `skipped` | `dry-run`
- `k8sgpt-auto-heal/pr-url`: Link to the created PR

## Security Considerations

- The watcher has **read-only** access to cluster resources (pods, deployments, etc.)
- It can only **annotate** K8sGPT Result CRDs (minimal write)
- All fixes go through **Git** — the watcher never `kubectl apply`s anything
- PRs require **human approval** before Flux reconciles
- Sensitive data (pod names, namespaces) can be anonymized via K8sGPT's `anonymized: true`
- For production: use a self-hosted LLM (LocalAI/Ollama) to keep data in-cluster

## Troubleshooting

**K8sGPT not producing Results:**
```bash
# Check K8sGPT pod logs
kubectl -n k8sgpt-operator-system logs -l app.kubernetes.io/name=k8sgpt --tail=50

# Verify the K8sGPT instance
kubectl -n k8sgpt-operator-system get k8sgpt -o yaml

# Check the OpenAI secret
kubectl -n k8sgpt-operator-system get secret k8sgpt-openai-secret
```

**Watcher not creating PRs:**
```bash
# Check watcher logs
kubectl -n k8sgpt-auto-heal logs -f deploy/auto-heal-watcher

# Verify GitHub token has repo scope
curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user

# Test in dry-run mode first
DRY_RUN=true ./scripts/04-deploy-watcher.sh local
```

**Flux not reconciling after PR merge:**
```bash
# Check Flux status
flux get all

# Force reconciliation
flux reconcile kustomization apps --with-source

# Check Flux logs
flux logs --level=error
```

## Teardown

```bash
./setup.sh teardown
```

This deletes the Kind cluster. The GitHub repo is preserved (delete manually if needed).

## License

MIT
