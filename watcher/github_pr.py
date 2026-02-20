"""
GitHub PR creation — branch, commit, and pull-request logic.
"""

import hashlib
from datetime import datetime, timezone

import yaml
from github import Github, GithubException

from config import (
    GITHUB_TOKEN,
    GITHUB_REPO,
    GITHUB_BRANCH,
    FLEET_APPS_PATH,
    OPENAI_MODEL,
    logger,
)


# ---------------------------------------------------------------------------
# Source-manifest discovery
# ---------------------------------------------------------------------------


def find_source_manifest(resource_name, resource_kind, flux_path=None):
    """Search the GitOps repo for the manifest that owns *resource_name*.

    When *flux_path* is provided (from Flux Kustomization inventory trace)
    it is used as the search directory.  Otherwise falls back to
    ``FLEET_APPS_PATH``.

    Returns ``(repo_path, raw_content)`` or ``(None, None)``.
    """
    search_path = flux_path or FLEET_APPS_PATH
    gh = Github(GITHUB_TOKEN)
    repo = gh.get_repo(GITHUB_REPO)

    try:
        contents = repo.get_contents(search_path, ref=GITHUB_BRANCH)
    except GithubException:
        logger.warning(f"Could not list {search_path} in repo — "
                       "falling back to full-manifest mode.")
        return None, None

    # Flatten one level of directories (non-recursive, covers the common case)
    files = []
    for item in contents:
        if item.type == "dir":
            try:
                files.extend(repo.get_contents(item.path, ref=GITHUB_BRANCH))
            except GithubException:
                pass
        else:
            files.append(item)

    for item in files:
        if not item.name.endswith((".yaml", ".yml")):
            continue
        try:
            raw = item.decoded_content.decode("utf-8")
            for doc in yaml.safe_load_all(raw):
                if doc is None:
                    continue
                meta = doc.get("metadata", {})
                kind = doc.get("kind", "")
                name = meta.get("name", "")
                if name == resource_name and kind == resource_kind:
                    logger.info(f"Found source manifest for {resource_kind}/{resource_name} "
                                f"at {item.path}")
                    return item.path, raw
        except Exception as e:
            logger.debug(f"Skipping {item.path}: {e}")

    logger.info(f"No source manifest found for {resource_kind}/{resource_name} in "
                f"{search_path} — LLM will generate a standalone manifest.")
    return None, None


def create_remediation_pr(sections, result_name, source_path=None):
    """Create a PR in the GitOps repo with the fix.

    When *source_path* is provided (discovered via ``find_source_manifest``)
    it takes precedence over the LLM-suggested MANIFEST_PATH to ensure the PR
    patches the original file in-place.
    """
    gh = Github(GITHUB_TOKEN)
    repo = gh.get_repo(GITHUB_REPO)

    pr_title = sections.get("PR_TITLE", f"fix: auto-remediation for {result_name}")
    pr_body = sections.get("PR_DESCRIPTION", "Auto-generated remediation by k8sgpt-auto-heal.")
    manifest_path = source_path or sections.get(
        "MANIFEST_PATH", f"{FLEET_APPS_PATH}/{result_name}.yaml"
    )
    manifest_content = sections.get("MANIFEST", "")

    if not manifest_content:
        logger.error("No manifest content in OpenAI response — skipping PR creation.")
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

    pr_body_full = _build_pr_body(pr_body, result_name)

    try:
        # Get the default branch SHA
        default_branch = repo.get_branch(GITHUB_BRANCH)
        sha = default_branch.commit.sha

        # Create a new branch
        repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=sha)
        logger.info(f"Created branch: {branch_name}")

        # Create or update the manifest file
        try:
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

        # Add labels (best-effort)
        try:
            pr.add_to_labels("auto-remediation", "k8sgpt", "needs-review")
        except GithubException:
            pass

        logger.info(f"Created PR #{pr.number}: {pr.html_url}")
        return pr.html_url

    except GithubException as e:
        logger.error(f"GitHub API error: {e}")
        return None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _build_pr_body(description, result_name):
    """Build the enriched PR body with metadata and review checklist."""
    return f"""## 🤖 Auto-Remediation PR

> Generated by **k8sgpt-auto-heal** watcher using OpenAI ({OPENAI_MODEL})

### Source
- **K8sGPT Result:** `{result_name}`
- **Timestamp:** {datetime.now(timezone.utc).isoformat()}
- **Mode:** OpenAI API

---

{description}

---

### ⚠️ Review Checklist
- [ ] Verify the fix is correct and safe
- [ ] Check that no security contexts were weakened
- [ ] Confirm resource limits are reasonable
- [ ] Validate the manifest applies cleanly

> **This PR requires human approval before merge.** Flux will reconcile once merged.
"""
