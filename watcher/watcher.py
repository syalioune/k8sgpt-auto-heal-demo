#!/usr/bin/env python3
"""
k8sgpt-auto-heal watcher

Watches K8sGPT Result CRDs and triggers Claude-powered remediation PRs.

Flow:
  1. Watch for new/updated Result CRDs in the K8sGPT namespace
  2. For each Result, gather context (pod spec, events, logs, deployment)
  3. Call Claude API to generate a remediation patch
  4. Create a GitHub PR with the fix in the GitOps fleet repo
  5. Annotate the Result CR to track remediation state
"""

import os
import sys
import json
import time
import yaml
import logging
import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from github import Github, GithubException
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
K8SGPT_NAMESPACE = os.getenv("K8SGPT_NAMESPACE", "k8sgpt-operator-system")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")  # "owner/repo" format
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
FLEET_APPS_PATH = os.getenv("FLEET_APPS_PATH", "apps/k8sgpt-demo")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
REMEDIATION_MODE = os.getenv("REMEDIATION_MODE", "api")  # "api" or "claude-code"
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "4096"))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# Annotation used to track which Results have been processed
ANNOTATION_REMEDIATION = "k8sgpt-auto-heal/remediation-state"
ANNOTATION_PR_URL = "k8sgpt-auto-heal/pr-url"

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("watcher")

# ---------------------------------------------------------------------------
# Kubernetes helpers
# ---------------------------------------------------------------------------


def init_k8s():
    """Initialize Kubernetes client (in-cluster or kubeconfig)."""
    try:
        config.load_incluster_config()
        logger.info("Loaded in-cluster Kubernetes config")
    except config.ConfigException:
        config.load_kube_config()
        logger.info("Loaded kubeconfig from default location")

    return client.CustomObjectsApi(), client.CoreV1Api(), client.AppsV1Api()


def get_result_crd_details(custom_api, name, namespace):
    """Fetch a K8sGPT Result CRD."""
    return custom_api.get_namespaced_custom_object(
        group="core.k8sgpt.ai",
        version="v1alpha1",
        namespace=namespace,
        plural="results",
        name=name,
    )


def get_pod_context(core_api, apps_api, pod_name, namespace):
    """Gather comprehensive context about a failing pod."""
    context = {}

    # Pod spec + status
    try:
        pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
        context["pod_spec"] = yaml.dump(
            client.ApiClient().sanitize_for_serialization(pod.spec),
            default_flow_style=False,
        )
        context["pod_status"] = yaml.dump(
            client.ApiClient().sanitize_for_serialization(pod.status),
            default_flow_style=False,
        )
    except ApiException as e:
        logger.warning(f"Could not fetch pod {namespace}/{pod_name}: {e.reason}")
        context["pod_spec"] = f"(unavailable: {e.reason})"

    # Pod logs (last 50 lines)
    try:
        logs = core_api.read_namespaced_pod_log(
            name=pod_name, namespace=namespace, tail_lines=50
        )
        context["pod_logs"] = logs
    except ApiException:
        context["pod_logs"] = "(no logs available)"

    # Events related to the pod
    try:
        events = core_api.list_namespaced_event(
            namespace=namespace,
            field_selector=f"involvedObject.name={pod_name}",
        )
        context["events"] = "\n".join(
            f"[{e.last_timestamp}] {e.reason}: {e.message}" for e in events.items[-10:]
        )
    except ApiException:
        context["events"] = "(no events)"

    # Owner deployment/replicaset spec
    try:
        pod_obj = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
        for owner in pod_obj.metadata.owner_references or []:
            if owner.kind == "ReplicaSet":
                rs = apps_api.read_namespaced_replica_set(
                    name=owner.name, namespace=namespace
                )
                for rs_owner in rs.metadata.owner_references or []:
                    if rs_owner.kind == "Deployment":
                        dep = apps_api.read_namespaced_deployment(
                            name=rs_owner.name, namespace=namespace
                        )
                        context["deployment_name"] = rs_owner.name
                        context["deployment_spec"] = yaml.dump(
                            client.ApiClient().sanitize_for_serialization(dep),
                            default_flow_style=False,
                        )
    except ApiException:
        pass

    return context


def get_service_context(core_api, service_name, namespace):
    """Gather context about a failing service."""
    context = {}
    try:
        svc = core_api.read_namespaced_service(name=service_name, namespace=namespace)
        context["service_spec"] = yaml.dump(
            client.ApiClient().sanitize_for_serialization(svc),
            default_flow_style=False,
        )
    except ApiException as e:
        context["service_spec"] = f"(unavailable: {e.reason})"

    try:
        endpoints = core_api.read_namespaced_endpoints(
            name=service_name, namespace=namespace
        )
        context["endpoints"] = yaml.dump(
            client.ApiClient().sanitize_for_serialization(endpoints),
            default_flow_style=False,
        )
    except ApiException:
        context["endpoints"] = "(no endpoints)"

    return context


def annotate_result(custom_api, name, namespace, annotations):
    """Annotate a Result CRD to track processing state."""
    body = {"metadata": {"annotations": annotations}}
    try:
        custom_api.patch_namespaced_custom_object(
            group="core.k8sgpt.ai",
            version="v1alpha1",
            namespace=namespace,
            plural="results",
            name=name,
            body=body,
        )
    except ApiException as e:
        logger.warning(f"Failed to annotate Result {name}: {e.reason}")


# ---------------------------------------------------------------------------
# Claude remediation
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert Kubernetes SRE assistant integrated into a GitOps auto-healing pipeline.

Your job:
1. Analyze the K8sGPT diagnostic result and the gathered cluster context.
2. Determine the root cause of the issue.
3. Generate a FIXED Kubernetes manifest (YAML) that resolves the problem.
4. Write a clear, concise PR description explaining:
   - What is broken and why
   - What the fix does
   - Any risks or caveats

RULES:
- Output ONLY valid YAML for the fix. Do NOT include kubectl commands.
- The manifest must be a complete, self-contained resource (not a patch).
- Preserve all existing labels, annotations, and metadata from the original.
- Follow security best practices (don't remove security contexts unless that IS the fix).
- If the fix involves adding volumes, resource limits, or config changes, explain why.
- If you cannot determine a safe fix, say so — do NOT guess.

OUTPUT FORMAT (use these exact markers):
---PR_TITLE---
<one-line PR title>
---PR_DESCRIPTION---
<markdown description of problem + fix>
---MANIFEST_PATH---
<relative path in the fleet repo, e.g. apps/k8sgpt-demo/nginx-deployment.yaml>
---MANIFEST---
<the complete fixed YAML manifest>
"""


def generate_remediation_via_api(result_details, k8s_context):
    """Call Claude API directly to generate remediation."""
    claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    user_message = f"""
## K8sGPT Diagnostic Result

**Resource Kind:** {result_details.get('spec', {}).get('kind', 'Unknown')}
**Resource Name:** {result_details.get('spec', {}).get('name', 'Unknown')}
**Backend:** {result_details.get('spec', {}).get('backend', 'Unknown')}

### Errors Detected
{json.dumps(result_details.get('spec', {}).get('error', []), indent=2)}

### AI Explanation
{result_details.get('spec', {}).get('details', '(none)')}

## Cluster Context

### Pod Spec
```yaml
{k8s_context.get('pod_spec', '(not applicable)')}
```

### Pod Logs (last 50 lines)
```
{k8s_context.get('pod_logs', '(not applicable)')}
```

### Events
```
{k8s_context.get('events', '(not applicable)')}
```

### Owner Deployment
```yaml
{k8s_context.get('deployment_spec', '(not applicable)')}
```

### Service Spec
```yaml
{k8s_context.get('service_spec', '(not applicable)')}
```

### Endpoints
```yaml
{k8s_context.get('endpoints', '(not applicable)')}
```

## GitOps Fleet Repo Info
- Apps path: {FLEET_APPS_PATH}
- The fixed manifest should be placed at a path under this directory.

Please generate the fix.
"""

    logger.info(f"Calling Claude API ({ANTHROPIC_MODEL}) for remediation...")

    response = claude.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    return response.content[0].text


def generate_remediation_via_claude_code(result_details, k8s_context):
    """Shell out to Claude Code CLI to generate remediation."""

    # Write context to a temp file for Claude Code to read
    context_path = "/tmp/k8sgpt-context.json"
    with open(context_path, "w") as f:
        json.dump(
            {
                "result": result_details.get("spec", {}),
                "cluster_context": k8s_context,
                "fleet_apps_path": FLEET_APPS_PATH,
            },
            f,
            indent=2,
        )

    prompt = f"""Read the K8sGPT diagnostic context from {context_path}.

Analyze the Kubernetes issue and generate a fix.

Output format (use these exact markers):
---PR_TITLE---
<one-line PR title>
---PR_DESCRIPTION---
<markdown description>
---MANIFEST_PATH---
<path under {FLEET_APPS_PATH}/>
---MANIFEST---
<complete fixed YAML>

Use the tools available to you:
- Read the context file
- If needed, run kubectl commands to gather more info
- Generate the fix manifest

{SYSTEM_PROMPT}
"""

    logger.info("Calling Claude Code CLI for remediation...")

    try:
        result = subprocess.run(
            [
                "claude",
                "--print",
                "--dangerously-skip-permissions",
                "--model", ANTHROPIC_MODEL,
                "-p", prompt,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env={
                **os.environ,
                "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
            },
        )
        if result.returncode != 0:
            logger.error(f"Claude Code failed: {result.stderr}")
            return None
        return result.stdout
    except FileNotFoundError:
        logger.error("Claude Code CLI not found. Install: npm install -g @anthropic-ai/claude-code")
        return None
    except subprocess.TimeoutExpired:
        logger.error("Claude Code timed out after 120s")
        return None


def parse_remediation_response(response_text):
    """Parse the structured output from Claude."""
    sections = {}
    current_section = None
    current_content = []

    for line in response_text.split("\n"):
        if line.strip().startswith("---") and line.strip().endswith("---"):
            if current_section:
                sections[current_section] = "\n".join(current_content).strip()
            current_section = line.strip().strip("-")
            current_content = []
        elif current_section:
            current_content.append(line)

    if current_section:
        sections[current_section] = "\n".join(current_content).strip()

    return sections


# ---------------------------------------------------------------------------
# GitHub PR creation
# ---------------------------------------------------------------------------


def create_remediation_pr(sections, result_name):
    """Create a PR in the GitOps fleet repo with the fix."""
    gh = Github(GITHUB_TOKEN)
    repo = gh.get_repo(GITHUB_REPO)

    pr_title = sections.get("PR_TITLE", f"fix: auto-remediation for {result_name}")
    pr_body = sections.get("PR_DESCRIPTION", "Auto-generated remediation by k8sgpt-auto-heal.")
    manifest_path = sections.get("MANIFEST_PATH", f"{FLEET_APPS_PATH}/{result_name}.yaml")
    manifest_content = sections.get("MANIFEST", "")

    if not manifest_content:
        logger.error("No manifest content in Claude response — skipping PR creation.")
        return None

    # Validate YAML
    try:
        yaml.safe_load(manifest_content)
    except yaml.YAMLError as e:
        logger.error(f"Generated manifest is not valid YAML: {e}")
        return None

    # Create a unique branch name
    short_hash = hashlib.md5(result_name.encode()).hexdigest()[:8]
    branch_name = f"auto-heal/{result_name[:40]}-{short_hash}"

    # Enrich PR body with metadata
    pr_body_full = f"""## 🤖 Auto-Remediation PR

> Generated by **k8sgpt-auto-heal** watcher using Claude ({ANTHROPIC_MODEL})

### Source
- **K8sGPT Result:** `{result_name}`
- **Timestamp:** {datetime.now(timezone.utc).isoformat()}
- **Mode:** {'Claude Code CLI' if REMEDIATION_MODE == 'claude-code' else 'Anthropic API'}

---

{pr_body}

---

### ⚠️ Review Checklist
- [ ] Verify the fix is correct and safe
- [ ] Check that no security contexts were weakened
- [ ] Confirm resource limits are reasonable
- [ ] Validate the manifest applies cleanly

> **This PR requires human approval before merge.** Flux will reconcile once merged.
"""

    try:
        # Get the default branch SHA
        default_branch = repo.get_branch(GITHUB_BRANCH)
        sha = default_branch.commit.sha

        # Create a new branch
        repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=sha)
        logger.info(f"Created branch: {branch_name}")

        # Create or update the manifest file
        try:
            # Check if file exists
            existing = repo.get_contents(manifest_path, ref=branch_name)
            repo.update_file(
                path=manifest_path,
                message=f"fix: {pr_title}",
                content=manifest_content,
                sha=existing.sha,
                branch=branch_name,
            )
        except GithubException:
            repo.create_file(
                path=manifest_path,
                message=f"fix: {pr_title}",
                content=manifest_content,
                branch=branch_name,
            )

        logger.info(f"Pushed manifest to {manifest_path} on branch {branch_name}")

        # Create the PR
        pr = repo.create_pull(
            title=f"🤖 {pr_title}",
            body=pr_body_full,
            head=branch_name,
            base=GITHUB_BRANCH,
        )

        # Add labels
        try:
            pr.add_to_labels("auto-remediation", "k8sgpt", "needs-review")
        except GithubException:
            pass  # Labels might not exist

        logger.info(f"Created PR #{pr.number}: {pr.html_url}")
        return pr.html_url

    except GithubException as e:
        logger.error(f"GitHub API error: {e}")
        return None


# ---------------------------------------------------------------------------
# Main watch loop
# ---------------------------------------------------------------------------


def process_result(custom_api, core_api, apps_api, result):
    """Process a single K8sGPT Result CRD."""
    metadata = result.get("metadata", {})
    name = metadata.get("name", "unknown")
    namespace = metadata.get("namespace", K8SGPT_NAMESPACE)
    annotations = metadata.get("annotations", {})
    spec = result.get("spec", {})

    # Skip if already processed
    if annotations.get(ANNOTATION_REMEDIATION) in ("pr-created", "skipped", "failed"):
        return

    resource_kind = spec.get("kind", "Unknown")
    resource_name = spec.get("name", "Unknown")
    errors = spec.get("error", [])
    details = spec.get("details", "")

    logger.info(f"Processing Result: {name} ({resource_kind}/{resource_name})")
    logger.info(f"  Errors: {errors}")
    logger.info(f"  AI Details: {details[:200]}...")

    # Mark as in-progress
    annotate_result(custom_api, name, namespace, {ANNOTATION_REMEDIATION: "in-progress"})

    # Gather context based on resource kind
    k8s_context = {}
    # resource_name format is typically "namespace/pod-name"
    parts = resource_name.split("/")
    res_namespace = parts[0] if len(parts) > 1 else "default"
    res_name = parts[-1]
    # Some Results include the container name in parens
    if "(" in res_name:
        res_name = res_name.split("(")[0]

    if resource_kind == "Pod":
        k8s_context = get_pod_context(core_api, apps_api, res_name, res_namespace)
    elif resource_kind == "Service":
        k8s_context = get_service_context(core_api, res_name, res_namespace)
    else:
        logger.info(f"  Resource kind '{resource_kind}' — gathering basic context")

    # Generate remediation
    if REMEDIATION_MODE == "claude-code":
        response_text = generate_remediation_via_claude_code(result, k8s_context)
    else:
        response_text = generate_remediation_via_api(result, k8s_context)

    if not response_text:
        annotate_result(custom_api, name, namespace, {ANNOTATION_REMEDIATION: "failed"})
        return

    logger.debug(f"Claude response:\n{response_text}")

    # Parse the structured response
    sections = parse_remediation_response(response_text)

    if not sections.get("MANIFEST"):
        logger.warning(f"  No MANIFEST section found in Claude response for {name}")
        annotate_result(custom_api, name, namespace, {ANNOTATION_REMEDIATION: "failed"})
        return

    # Create PR (or dry-run)
    if DRY_RUN:
        logger.info(f"  [DRY RUN] Would create PR for {name}:")
        logger.info(f"    Title: {sections.get('PR_TITLE', '(none)')}")
        logger.info(f"    Path: {sections.get('MANIFEST_PATH', '(none)')}")
        logger.info(f"    Manifest:\n{sections.get('MANIFEST', '(none)')[:500]}")
        annotate_result(custom_api, name, namespace, {ANNOTATION_REMEDIATION: "dry-run"})
        return

    pr_url = create_remediation_pr(sections, name)

    if pr_url:
        annotate_result(
            custom_api,
            name,
            namespace,
            {
                ANNOTATION_REMEDIATION: "pr-created",
                ANNOTATION_PR_URL: pr_url,
            },
        )
        logger.info(f"  ✅ Remediation PR created: {pr_url}")
    else:
        annotate_result(custom_api, name, namespace, {ANNOTATION_REMEDIATION: "failed"})
        logger.error(f"  ❌ Failed to create PR for {name}")


def run_watcher():
    """Main watch loop for K8sGPT Result CRDs."""
    custom_api, core_api, apps_api = init_k8s()

    logger.info("=" * 60)
    logger.info("k8sgpt-auto-heal watcher started")
    logger.info(f"  Namespace:  {K8SGPT_NAMESPACE}")
    logger.info(f"  Model:      {ANTHROPIC_MODEL}")
    logger.info(f"  Mode:       {REMEDIATION_MODE}")
    logger.info(f"  Fleet repo: {GITHUB_REPO}")
    logger.info(f"  Fleet path: {FLEET_APPS_PATH}")
    logger.info(f"  Dry run:    {DRY_RUN}")
    logger.info("=" * 60)

    # First, process any existing Results
    logger.info("Scanning for existing unprocessed Results...")
    try:
        existing = custom_api.list_namespaced_custom_object(
            group="core.k8sgpt.ai",
            version="v1alpha1",
            namespace=K8SGPT_NAMESPACE,
            plural="results",
        )
        for item in existing.get("items", []):
            try:
                process_result(custom_api, core_api, apps_api, item)
            except Exception as e:
                logger.exception(f"Error processing existing result: {e}")
    except ApiException as e:
        logger.warning(f"Could not list existing results: {e.reason}")

    # Now watch for new/updated Results
    logger.info("Watching for new K8sGPT Results...")
    resource_version = ""
    while True:
        try:
            w = watch.Watch()
            stream = w.stream(
                custom_api.list_namespaced_custom_object,
                group="core.k8sgpt.ai",
                version="v1alpha1",
                namespace=K8SGPT_NAMESPACE,
                plural="results",
                resource_version=resource_version,
                timeout_seconds=300,
            )
            for event in stream:
                event_type = event["type"]
                result = event["object"]
                name = result.get("metadata", {}).get("name", "?")
                resource_version = result.get("metadata", {}).get("resourceVersion", "")

                if event_type in ("ADDED", "MODIFIED"):
                    logger.info(f"Event: {event_type} Result/{name}")
                    try:
                        process_result(custom_api, core_api, apps_api, result)
                    except Exception as e:
                        logger.exception(f"Error processing {name}: {e}")

        except ApiException as e:
            if e.status == 410:
                logger.info("Watch expired (410 Gone), restarting...")
                resource_version = ""
            else:
                logger.error(f"Watch error: {e}")
                time.sleep(10)
        except Exception as e:
            logger.exception(f"Unexpected error in watch loop: {e}")
            time.sleep(10)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Validate config
    missing = []
    if not GITHUB_TOKEN:
        missing.append("GITHUB_TOKEN")
    if not GITHUB_REPO:
        missing.append("GITHUB_REPO")
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")

    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        logger.error("Copy .env.example to .env and fill in the values.")
        sys.exit(1)

    run_watcher()
