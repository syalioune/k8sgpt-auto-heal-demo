"""
Kubernetes helpers — client initialisation, context gathering, CRD annotation.
"""

import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from config import logger


# ---------------------------------------------------------------------------
# Client initialisation
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


# ---------------------------------------------------------------------------
# Result CRD
# ---------------------------------------------------------------------------


def get_result_crd_details(custom_api, name, namespace):
    """Fetch a K8sGPT Result CRD."""
    return custom_api.get_namespaced_custom_object(
        group="core.k8sgpt.ai",
        version="v1alpha1",
        namespace=namespace,
        plural="results",
        name=name,
    )


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
# Context gathering
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Flux trace — Kustomization inventory reverse-lookup
# ---------------------------------------------------------------------------

# Map common Kubernetes kinds → API group for inventory ID matching.
_GROUP_MAP = {
    "Deployment": "apps",
    "StatefulSet": "apps",
    "DaemonSet": "apps",
    "ReplicaSet": "apps",
    "Service": "",
    "ConfigMap": "",
    "Secret": "",
    "Pod": "",
    "Namespace": "",
    "PersistentVolumeClaim": "",
    "Ingress": "networking.k8s.io",
    "NetworkPolicy": "networking.k8s.io",
    "HorizontalPodAutoscaler": "autoscaling",
    "CronJob": "batch",
    "Job": "batch",
}


def trace_flux_source(custom_api, resource_name, resource_kind, resource_namespace):
    """Trace a Kubernetes resource back to its Flux Kustomization source path.

    Replicates ``flux trace`` logic using the K8s API:

    1. List all Flux Kustomizations in the ``flux-system`` namespace.
    2. For each, check ``status.inventory.entries`` for the target resource.
    3. Return the ``spec.path`` (repo-relative directory) of the owning
       Kustomization, or ``None`` if the resource is unmanaged.

    The inventory entry ID format is ``<namespace>_<name>_<group>_<Kind>``.
    """
    api_group = _GROUP_MAP.get(resource_kind, "")

    # Build the expected inventory entry ID
    target_id = f"{resource_namespace}_{resource_name}_{api_group}_{resource_kind}"

    try:
        kustomizations = custom_api.list_namespaced_custom_object(
            group="kustomize.toolkit.fluxcd.io",
            version="v1",
            namespace="flux-system",
            plural="kustomizations",
        )
    except ApiException as e:
        logger.warning(f"Could not list Flux Kustomizations: {e.reason}")
        return None

    for ks in kustomizations.get("items", []):
        ks_name = ks.get("metadata", {}).get("name", "?")
        inventory = ks.get("status", {}).get("inventory", {}) or {}
        entries = inventory.get("entries", []) or []

        for entry in entries:
            entry_id = entry.get("id", "")
            # Match exactly or with a trailing _<version> suffix
            if entry_id == target_id or entry_id.startswith(target_id + "_"):
                raw_path = ks.get("spec", {}).get("path", ".")
                # Normalise: strip leading "./"
                path = raw_path.lstrip("./") if raw_path not in (".", "") else ""
                source_ref = ks.get("spec", {}).get("sourceRef", {})
                logger.info(
                    f"Flux trace: {resource_kind}/{resource_name} → "
                    f"Kustomization/{ks_name} → path='{path}' "
                    f"(source: {source_ref.get('kind', '?')}/"
                    f"{source_ref.get('name', '?')})"
                )
                return path

    logger.info(
        f"Flux trace: no Kustomization found managing "
        f"{resource_kind}/{resource_name} in {resource_namespace}"
    )
    return None


# ---------------------------------------------------------------------------
# Service context
# ---------------------------------------------------------------------------


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
