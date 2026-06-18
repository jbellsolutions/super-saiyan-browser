#!/usr/bin/env python3
"""
Escalation rank 2: Airtop — cloud scale page-query and GTM automations.

Cloud SaaS for GTM and scheduled workflows. Conversational agent builder,
built-in scheduling, pre-built templates. REST API + webhooks.

Best for:
- Non-developer workflows (marketing/sales build agents without code)
- Scheduled monitoring (built-in vs coding cron)
- SOC2/HIPAA compliance requirements

Prerequisites:
    Sign up at https://airtop.ai
    API key from https://portal.airtop.ai/api-keys
    Build an agent in the conversational UI first

Usage: python rank2_airtop.py
"""

import os
import sys
import json
import time
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_KEY = os.environ.get("AIRTOP_API_KEY", "at_YOUR_KEY_HERE")
BASE_URL = "https://api.airtop.ai/api"

# These come from the Airtop UI after building an agent:
# - Agent ID: found in agent dashboard
# - Webhook ID: created in agent settings → webhooks
AGENT_ID = os.environ.get("AIRTOP_AGENT_ID", "your-agent-id")
WEBHOOK_ID = os.environ.get("AIRTOP_WEBHOOK_ID", "your-webhook-id")


# ---------------------------------------------------------------------------
# Helper: Make authenticated request
# ---------------------------------------------------------------------------

def airtop_request(method: str, path: str, **kwargs) -> dict:
    """Make an authenticated request to the Airtop API."""
    url = f"{BASE_URL}/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    resp = requests.request(method, url, headers=headers, **kwargs)

    if resp.status_code == 429:
        raise RuntimeError("Rate limited — wait before retrying")
    if resp.status_code == 402:
        raise RuntimeError("Out of credits — upgrade plan")
    resp.raise_for_status()

    return resp.json()


# ---------------------------------------------------------------------------
# Pattern 1: Trigger Agent via Webhook
# ---------------------------------------------------------------------------

def trigger_agent(target_url: str, extra_vars: dict = None) -> str:
    """
    Trigger an Airtop agent to run against a specific URL.

    Returns the invocation ID for polling results.

    Args:
        target_url: The URL for the agent to visit
        extra_vars: Additional config variables to pass to the agent

    Returns:
        invocation_id: String ID to poll for results
    """
    print(f"\n🚀 Triggering agent for: {target_url}")

    config_vars = {"url": target_url}
    if extra_vars:
        config_vars.update(extra_vars)

    result = airtop_request(
        "POST",
        f"/hooks/agents/{AGENT_ID}/webhooks/{WEBHOOK_ID}",
        json={"configVars": config_vars},
    )

    invocation_id = result.get("invocationId") or result.get("id")
    print(f"  Invocation ID: {invocation_id}")
    return invocation_id


# ---------------------------------------------------------------------------
# Pattern 2: Poll for Results
# ---------------------------------------------------------------------------

def wait_for_result(invocation_id: str, timeout: int = 300, poll_interval: int = 5) -> dict:
    """
    Poll for agent execution results.

    Args:
        invocation_id: The invocation ID from trigger_agent()
        timeout: Max seconds to wait
        poll_interval: Seconds between polls

    Returns:
        dict with the invocation result data
    """
    print(f"\n⏳ Waiting for results (timeout: {timeout}s)...")

    start = time.time()

    while time.time() - start < timeout:
        result = airtop_request("GET", f"/invocations/{invocation_id}")

        status = result.get("status", "unknown")
        print(f"  Status: {status} ({time.time() - start:.0f}s elapsed)")

        if status in ("completed", "success", "done"):
            print(f"  ✓ Complete!")
            return result
        if status in ("failed", "error", "cancelled"):
            error = result.get("error", "Unknown error")
            raise RuntimeError(f"Agent failed: {error}")

        time.sleep(poll_interval)

    raise TimeoutError(f"Agent did not complete within {timeout}s")


# ---------------------------------------------------------------------------
# Pattern 3: Full Run (Trigger + Wait + Parse)
# ---------------------------------------------------------------------------

def run_airtop_agent(target_url: str, task_description: str = "") -> dict:
    """
    Complete Airtop agent lifecycle: trigger, wait, parse.

    Args:
        target_url: URL for the agent to visit
        task_description: Optional natural language task for the agent

    Returns:
        Parsed results dict
    """
    extra_vars = {}
    if task_description:
        extra_vars["task"] = task_description

    invocation_id = trigger_agent(target_url, extra_vars)
    result = wait_for_result(invocation_id)

    # Parse output (structure depends on agent configuration)
    output = result.get("output") or result.get("result") or result.get("data", {})

    print(f"\n📋 Result keys: {list(output.keys()) if isinstance(output, dict) else 'scalar'}")
    return output


# ---------------------------------------------------------------------------
# Pattern 4: Scheduled Workflow (Cron-Ready)
# ---------------------------------------------------------------------------

def scheduled_monitoring(targets: list[dict]) -> list[dict]:
    """
    Run the same agent against multiple targets sequentially.

    Designed for cron jobs. Each target runs independently so one
    failure doesn't block the rest.

    Args:
        targets: List of {"url": "...", "task": "..."}

    Returns:
        List of results with status
    """
    results = []

    for i, target in enumerate(targets):
        print(f"\n📌 Target {i+1}/{len(targets)}: {target['url']}")
        try:
            output = run_airtop_agent(target["url"], target.get("task", ""))
            results.append({"url": target["url"], "status": "success", "output": output})
        except Exception as e:
            print(f"  ❌ Failed: {e}")
            results.append({"url": target["url"], "status": "failed", "error": str(e)})

    # Summary
    succeeded = sum(1 for r in results if r["status"] == "success")
    print(f"\n✅ {succeeded}/{len(targets)} targets succeeded")

    return results


# ---------------------------------------------------------------------------
# Pattern 5: Webhook Receiver (for Hermes integration)
# ---------------------------------------------------------------------------

"""
Airtop can call your webhook when an agent completes. Set this up in
the Airtop UI under agent settings → webhooks → outgoing.

Your webhook receives:
{
    "invocationId": "...",
    "status": "completed",
    "output": { ... },
    "timestamp": "2026-06-05T14:00:00Z"
}

To receive these in Hermes, set up a webhook subscription:
    hermes webhook create --name "airtop-callback" --secret "your-secret"
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if API_KEY == "at_YOUR_KEY_HERE":
        print("❌ Set AIRTOP_API_KEY, AIRTOP_AGENT_ID, AIRTOP_WEBHOOK_ID env vars.")
        print("   Sign up at https://airtop.ai → Portal → API Keys")
        print("   Build an agent in the UI → Settings → Webhooks")
        sys.exit(1)

    # Quick test: run against a simple URL
    result = run_airtop_agent(
        target_url="https://example.com",
        task_description="Extract the main heading and all paragraph text",
    )

    print(f"\n✅ Result: {json.dumps(result, indent=2)[:500]}")
