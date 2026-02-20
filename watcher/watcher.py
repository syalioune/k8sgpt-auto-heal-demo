#!/usr/bin/env python3
"""
k8sgpt-auto-heal watcher

Watches K8sGPT Result CRDs and triggers OpenAI-powered remediation PRs.

Flow:
  1. Watch for new/updated Result CRDs in the K8sGPT namespace
  2. For each Result, gather context (pod spec, events, logs, deployment)
  3. Call OpenAI API to generate a remediation patch
  4. Create a GitHub PR with the fix in the GitOps repo
  5. Annotate the Result CR to track remediation state
"""

import sys
import time

from kubernetes import watch
from kubernetes.client.rest import ApiException

from config import (
    K8SGPT_NAMESPACE,
    OPENAI_MODEL,
    GITHUB_TOKEN,
    GITHUB_REPO,
    OPENAI_API_KEY,
    FLEET_APPS_PATH,
    DRY_RUN,
    ANNOTATION_REMEDIATION,
    ANNOTATION_PR_URL,
    logger,
)
from k8s_helpers import (
    init_k8s,
    annotate_result,
    get_pod_context,
    get_service_context,
    trace_flux_source,
)
from remediation import generate_remediation, parse_response
from github_pr import create_remediation_pr, find_source_manifest


# ---------------------------------------------------------------------------
# Result processing
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
    parts = resource_name.split("/")
    res_namespace = parts[0] if len(parts) > 1 else "default"
    res_name = parts[-1]
    if "(" in res_name:
        res_name = res_name.split("(")[0]

    if resource_kind == "Pod":
        k8s_context = get_pod_context(core_api, apps_api, res_name, res_namespace)
    elif resource_kind == "Service":
        k8s_context = get_service_context(core_api, res_name, res_namespace)
    else:
        logger.info(f"  Resource kind '{resource_kind}' — gathering basic context")

    # Resolve the owning resource name for manifest lookup.
    # For Pods the owner Deployment name is more useful than the pod name.
    lookup_name = k8s_context.get("deployment_name", res_name)
    lookup_kind = "Deployment" if "deployment_name" in k8s_context else resource_kind

    # Trace the resource back through Flux Kustomization inventory
    # (replicates `flux trace` using the K8s API)
    flux_path = trace_flux_source(custom_api, lookup_name, lookup_kind, res_namespace)

    # Look up the original source manifest in the GitOps repo
    source_path, source_content = find_source_manifest(
        lookup_name, lookup_kind, flux_path=flux_path
    )
    source_manifest = (source_path, source_content) if source_path else None

    # Generate remediation via OpenAI API
    response_text = generate_remediation(result, k8s_context, source_manifest, flux_path=flux_path)

    if not response_text:
        annotate_result(custom_api, name, namespace, {ANNOTATION_REMEDIATION: "failed"})
        return

    logger.debug(f"OpenAI response:\n{response_text}")

    # Parse the structured response
    sections = parse_response(response_text)

    if not sections.get("MANIFEST"):
        logger.warning(f"  No MANIFEST section found in OpenAI response for {name}")
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

    pr_url = create_remediation_pr(sections, name, source_path=source_path)

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


# ---------------------------------------------------------------------------
# Watch loop
# ---------------------------------------------------------------------------


def run_watcher():
    """Main watch loop for K8sGPT Result CRDs."""
    custom_api, core_api, apps_api = init_k8s()

    logger.info("=" * 60)
    logger.info("k8sgpt-auto-heal watcher started")
    logger.info(f"  Namespace:  {K8SGPT_NAMESPACE}")
    logger.info(f"  Model:      {OPENAI_MODEL}")
    logger.info(f"  Repo:       {GITHUB_REPO}")
    logger.info(f"  Apps path:  {FLEET_APPS_PATH}")
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
    missing = []
    if not GITHUB_TOKEN:
        missing.append("GITHUB_TOKEN")
    if not GITHUB_REPO:
        missing.append("GITHUB_REPO")
    if not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")

    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        logger.error("Copy .env.example to .env and fill in the values.")
        sys.exit(1)

    run_watcher()
