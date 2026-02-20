"""
Centralised configuration — all environment variables and logging setup.
"""

import os
import logging

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
K8SGPT_NAMESPACE = os.getenv("K8SGPT_NAMESPACE", "k8sgpt-operator-system")

# GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")  # "owner/repo" format
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
FLEET_APPS_PATH = os.getenv("FLEET_APPS_PATH", "apps/k8sgpt-demo")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "4096"))

# Behaviour
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Annotation keys used to track processing state on Result CRDs
# ---------------------------------------------------------------------------

ANNOTATION_REMEDIATION = "k8sgpt-auto-heal/remediation-state"
ANNOTATION_PR_URL = "k8sgpt-auto-heal/pr-url"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("watcher")
