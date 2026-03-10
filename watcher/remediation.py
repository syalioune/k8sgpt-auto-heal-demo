"""
OpenAI remediation — prompt construction, API call, response parsing.
"""

import json

import openai

from config import (
    OPENAI_API_KEY,
    OPENAI_MODEL,
    MAX_TOKENS,
    FLEET_APPS_PATH,
    logger,
)

# ---------------------------------------------------------------------------
# System prompt
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
- If an ORIGINAL MANIFEST is provided, you MUST patch it in-place:
  • Keep the same file path (MANIFEST_PATH) as the original.
  • Preserve ALL existing metadata, labels, annotations, and fields.
  • Change ONLY the minimal set of fields required to fix the issue.
- If NO original manifest is provided, generate a complete, self-contained resource.
- Follow security best practices (don't remove security contexts unless that IS the fix).
- If the fix involves adding volumes, resource limits, or config changes, explain why.
- If you cannot determine a safe fix, say so — do NOT guess.

OUTPUT FORMAT (use these exact markers):
---PR_TITLE---
<one-line PR title>
---PR_DESCRIPTION---
<markdown description of problem + fix>
---MANIFEST_PATH---
<relative path in the repo — MUST match the original file path when one is provided>
---MANIFEST---
<the complete fixed YAML manifest — raw YAML only, NO markdown code fences>

CRITICAL: Do NOT wrap any section in markdown code fences (```). Output raw text only.
"""

# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------


def generate_remediation(result_details, k8s_context, source_manifest=None, flux_path=None):
    """Call OpenAI API to generate remediation.

    Args:
        result_details: K8sGPT Result CRD dict.
        k8s_context: Cluster context gathered by k8s_helpers.
        source_manifest: Optional ``(repo_path, yaml_content)`` tuple of the
            original manifest found in the GitOps repo.  When provided the LLM
            is asked to patch this file in-place rather than generating a new
            one.
        flux_path: Optional repo-relative directory discovered via Flux
            Kustomization inventory trace (replicates ``flux trace``).
    """
    oai = openai.OpenAI(api_key=OPENAI_API_KEY)

    user_message = _build_user_message(result_details, k8s_context, source_manifest, flux_path)

    logger.info(f"Calling OpenAI API ({OPENAI_MODEL}) for remediation...")

    response = oai.chat.completions.create(
        model=OPENAI_MODEL,
        max_tokens=MAX_TOKENS,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )

    return response.choices[0].message.content


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def parse_response(response_text):
    """Parse the structured output from OpenAI."""
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

    # Strip markdown code fences the LLM sometimes wraps around sections
    for key in ("MANIFEST", "MANIFEST_PATH"):
        if key in sections:
            sections[key] = _strip_code_fences(sections[key])

    return sections


def _strip_code_fences(text):
    """Remove markdown code fences (```yaml ... ```) from text."""
    lines = text.split("\n")
    # Strip leading blank lines, then the opening fence (e.g. ```yaml, ```yml, ```)
    while lines and lines[0].strip() == "":
        lines = lines[1:]
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    # Strip trailing blank lines, then the closing fence
    while lines and lines[-1].strip() == "":
        lines = lines[:-1]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _build_user_message(result_details, k8s_context, source_manifest=None, flux_path=None):
    """Build the user message sent to OpenAI."""

    # Original manifest section (when found in the repo)
    if source_manifest and source_manifest[0]:
        source_path, source_content = source_manifest
        original_section = f"""
## Original Manifest (from Git — source of truth)

**File:** `{source_path}`

```yaml
{source_content}
```

> IMPORTANT: Patch this file in-place.  Your MANIFEST_PATH **must** be `{source_path}`.
> Change only what is necessary to fix the issue.  Preserve everything else.
"""
    else:
        original_section = """
## Original Manifest

(Not found in the GitOps repo — generate a complete standalone resource.)
"""

    return f"""
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
{original_section}
## GitOps Context (Flux)
- Kustomization source path: {flux_path or FLEET_APPS_PATH}
- Apps path: {FLEET_APPS_PATH}
- The fixed manifest should be placed at a path under the Kustomization source path.

Please generate the fix.
"""
