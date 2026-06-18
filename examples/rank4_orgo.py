#!/usr/bin/env python3
"""
Escalation rank 4: Orgo Machines — full desktop cloud VMs for AI agents.

When browser automation hits a wall, you need a real computer.
Orgo provides sub-500ms boot cloud VMs with full Linux desktops.

Best for:
- Desktop applications (not web apps)
- Multi-window workflows
- File system operations + browser simultaneously
- Local development environments
- GPU-accelerated workloads (roadmap)
- Complex software without web interfaces

Prerequisites:
    pip install orgo-sdk  # (hypothetical — check orgo.ai for actual package)
    export ORGO_API_KEY="org_..."

Usage: python rank4_orgo.py
"""

import os
import sys
import json
import time
import base64


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_KEY = os.environ.get("ORGO_API_KEY", "org_YOUR_KEY_HERE")
BASE_URL = "https://api.orgo.ai/v1"


# ============================================================================
# NOTE: This file is an ILLUSTRATIVE SKETCH. The `OrgoClient` below is a
# simulation, not a working client — it does not call Orgo.
#
# The REAL, production integration lives in src/super_browser/adapters.py
# (`OrgoAdapter`), which calls Orgo's live HTTP API and is exercised by the
# test suite. To actually run an Orgo task, use the router instead of this file:
#
#     super-browser run --goal "<desktop task>"      # routes to Orgo when appropriate
#
# Check https://orgo.ai/docs for the current SDK/API surface.
# ============================================================================


# ---------------------------------------------------------------------------
# Helper: Simulated OrgoClient
# ---------------------------------------------------------------------------

class OrgoClient:
    """
    Simulated Orgo client. Replace with actual SDK when available.

    Real SDK would be:
        from orgo import OrgoClient
        client = OrgoClient(api_key="org_...")
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        # In reality: self._client = OrgoSDK(api_key)

    def create_vm(self, template: str = "ubuntu-desktop", plan: str = "hacker") -> dict:
        """
        Create a new cloud VM. Sub-500ms boot time.

        Args:
            template: Docker image template (ubuntu-desktop, custom)
            plan: Pricing plan (hacker, team, scale)

        Returns:
            VM info dict with vm_id
        """
        print(f"🖥️  Creating VM: {template} ({plan} plan)")
        # In reality: response = self._client.vms.create(template=template)
        return {"vm_id": "vm_abc123", "status": "booting"}

    def wait_ready(self, vm_id: str, timeout: int = 30) -> bool:
        """
        Wait for VM to be ready. Usually sub-500ms.
        """
        print(f"⏳ Waiting for VM {vm_id}...")
        # In reality: self._client.vms.wait_ready(vm_id, timeout)
        time.sleep(0.5)  # Simulating sub-500ms boot
        print(f"  ✓ VM ready")
        return True

    def execute(self, vm_id: str, command: str) -> dict:
        """
        Execute a shell command inside the VM.

        Returns:
            {"stdout": "...", "stderr": "...", "exit_code": 0}
        """
        print(f"  $ {command[:80]}...")
        # In reality: return self._client.vms.execute(vm_id, command)
        return {"stdout": "command output here", "stderr": "", "exit_code": 0}

    def screenshot(self, vm_id: str) -> bytes:
        """
        Take a screenshot of the VM desktop.

        Returns:
            PNG image bytes
        """
        # In reality: return self._client.vms.screenshot(vm_id)
        return b"fake_png_data"

    def upload(self, vm_id: str, local_path: str, remote_path: str):
        """Upload a file to the VM."""
        print(f"  📤 Upload: {local_path} → {remote_path}")
        # In reality: self._client.vms.upload(vm_id, local_path, remote_path)

    def download(self, vm_id: str, remote_path: str, local_path: str):
        """Download a file from the VM."""
        print(f"  📥 Download: {remote_path} → {local_path}")
        # In reality: self._client.vms.download(vm_id, remote_path, local_path)

    def terminate(self, vm_id: str):
        """Terminate and clean up the VM."""
        print(f"🗑️  Terminating VM {vm_id}")
        # In reality: self._client.vms.terminate(vm_id)


# ---------------------------------------------------------------------------
# Pattern 1: Install & Run Desktop App
# ---------------------------------------------------------------------------

def run_desktop_app(client: OrgoClient, app_name: str, install_cmd: str, run_cmd: str):
    """
    When you need to automate a desktop application that has no web interface.

    Example: Install LibreOffice, open a document, export as PDF.
    """
    vm = client.create_vm(template="ubuntu-desktop")
    client.wait_ready(vm["vm_id"])

    try:
        # Install the application
        print(f"\n📦 Installing {app_name}...")
        result = client.execute(vm["vm_id"], install_cmd)
        if result["exit_code"] != 0:
            raise RuntimeError(f"Install failed: {result['stderr']}")

        # Run the application
        print(f"🚀 Running {app_name}...")
        result = client.execute(vm["vm_id"], run_cmd)

        # Take a screenshot to verify
        screenshot = client.screenshot(vm["vm_id"])
        with open(f"/tmp/{app_name}_output.png", "wb") as f:
            f.write(screenshot)

        print(f"✅ {app_name} completed successfully")
        return result

    finally:
        client.terminate(vm["vm_id"])


# ---------------------------------------------------------------------------
# Pattern 2: Browser Automation Inside VM
# ---------------------------------------------------------------------------

def browser_inside_vm(client: OrgoClient, target_url: str, script_path: str):
    """
    Run Playwright/Selenium INSIDE the Orgo VM.

    This gives you full control over the browser — no CDP limits,
    no cloud browser restrictions. You can install extensions,
    modify browser flags, run headed mode, etc.
    """
    vm = client.create_vm(template="ubuntu-desktop")
    client.wait_ready(vm["vm_id"])

    try:
        # Install Playwright + Chromium inside the VM
        print("\n📦 Setting up Playwright inside VM...")
        client.execute(vm["vm_id"], "pip install playwright")
        client.execute(vm["vm_id"], "playwright install chromium")

        # Upload the scraping script
        client.upload(vm["vm_id"], script_path, "/home/agent/scrape.py")

        # Run the script with the target URL
        print(f"🚀 Running browser automation against {target_url}...")
        result = client.execute(
            vm["vm_id"],
            f"python3 /home/agent/scrape.py --url {target_url}"
        )

        # Download results
        client.download(
            vm["vm_id"],
            "/home/agent/output.json",
            "/tmp/vm_scrape_output.json"
        )

        print("✅ Browser automation inside VM complete")
        return result

    finally:
        client.terminate(vm["vm_id"])


# ---------------------------------------------------------------------------
# Pattern 3: Multi-Window Workflow
# ---------------------------------------------------------------------------

def multi_window_workflow(client: OrgoClient):
    """
    Open multiple applications simultaneously and coordinate between them.

    Example: Open a browser, copy data, paste into a spreadsheet, save.
    """
    vm = client.create_vm(template="ubuntu-desktop")
    client.wait_ready(vm["vm_id"])

    try:
        # Setup: install needed tools
        client.execute(vm["vm_id"], "pip install playwright openpyxl")
        client.execute(vm["vm_id"], "playwright install chromium")

        # Step 1: Open browser and scrape
        print("\n1️⃣ Scraping data from web...")
        client.execute(vm["vm_id"], """
            python3 -c "
from playwright.sync_api import sync_playwright
import json

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.goto('https://example.com/data')
    data = page.evaluate('() => document.querySelectorAll(\"tr\")')
    with open('/home/agent/scraped.json', 'w') as f:
        json.dump({'rows': len(data)}, f)
    browser.close()
"
        """)

        # Step 2: Process in spreadsheet
        print("\n2️⃣ Processing in spreadsheet...")
        client.execute(vm["vm_id"], """
            python3 -c "
import json
from openpyxl import Workbook

wb = Workbook()
ws = wb.active
ws.title = 'Scraped Data'

with open('/home/agent/scraped.json') as f:
    data = json.load(f)

ws['A1'] = f'Total rows: {data[\"rows\"]}'
wb.save('/home/agent/output.xlsx')
print('Spreadsheet saved')
"
        """)

        # Step 3: Download the result
        client.download(vm["vm_id"], "/home/agent/output.xlsx", "/tmp/workflow_output.xlsx")
        print("✅ Multi-window workflow complete")

    finally:
        client.terminate(vm["vm_id"])


# ---------------------------------------------------------------------------
# Pattern 4: File Processing Pipeline
# ---------------------------------------------------------------------------

def file_processing_pipeline(client: OrgoClient, input_file: str):
    """
    Upload a file, process it with multiple tools, download results.

    Use case: PDF → OCR → data extraction → JSON → analysis.
    """
    vm = client.create_vm(template="ubuntu-desktop")
    client.wait_ready(vm["vm_id"])

    try:
        # Upload the input file
        client.upload(vm["vm_id"], input_file, "/home/agent/input.pdf")

        # Install processing tools
        print("\n📦 Installing processing tools...")
        client.execute(vm["vm_id"], "pip install pymupdf marker-pdf pandas")

        # Process
        print("\n🔄 Processing file...")
        client.execute(vm["vm_id"], """
            python3 -c "
import fitz  # pymupdf

doc = fitz.open('/home/agent/input.pdf')
text = ''
for page in doc:
    text += page.get_text()

with open('/home/agent/extracted.txt', 'w') as f:
    f.write(text)

print(f'Extracted {len(text)} chars from {len(doc)} pages')
"
        """)

        # Download results
        client.download(vm["vm_id"], "/home/agent/extracted.txt", "/tmp/extracted.txt")
        print("✅ File processing complete")

    finally:
        client.terminate(vm["vm_id"])


# ---------------------------------------------------------------------------
# Pattern 5: Development Environment
# ---------------------------------------------------------------------------

def dev_environment(client: OrgoClient, repo_url: str, test_command: str):
    """
    Spin up a full dev environment, clone a repo, run tests.

    Use case: Isolated CI/CD, testing in clean environment, code review.
    """
    vm = client.create_vm(template="ubuntu-desktop", plan="scale")  # 4 vCPU, 16GB
    client.wait_ready(vm["vm_id"])

    try:
        # Clone repo
        print(f"\n📦 Cloning {repo_url}...")
        client.execute(vm["vm_id"], f"git clone {repo_url} /home/agent/repo")

        # Install dependencies
        print("📦 Installing dependencies...")
        client.execute(vm["vm_id"], "cd /home/agent/repo && pip install -r requirements.txt")

        # Run tests
        print(f"🧪 Running tests: {test_command}")
        result = client.execute(
            vm["vm_id"],
            f"cd /home/agent/repo && {test_command}"
        )

        print(f"\n📋 Test output:\n{result['stdout']}")
        if result["stderr"]:
            print(f"⚠️  Stderr:\n{result['stderr']}")

        return result

    finally:
        client.terminate(vm["vm_id"])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if API_KEY == "org_YOUR_KEY_HERE":
        print("❌ Set ORGO_API_KEY environment variable first.")
        print("   Sign up at https://orgo.ai → Dashboard → API Keys")
        sys.exit(1)

    client = OrgoClient(API_KEY)
    print("✓ Orgo client initialized")
    print("\n⚠️  This is a conceptual example. Check https://orgo.ai/docs for the actual SDK.")
    print("   The patterns (install app, run browser, multi-window, file processing,")
    print("   dev environment) are correct regardless of the SDK specifics.\n")

    # Demo: create a VM, run a command, terminate
    vm = client.create_vm(template="ubuntu-desktop")
    client.wait_ready(vm["vm_id"])
    result = client.execute(vm["vm_id"], "echo 'Hello from Orgo VM!' && uname -a")
    print(f"\n✅ VM says: {result['stdout'].strip()}")
    client.terminate(vm["vm_id"])
    print("\n🏁 Demo complete")