import unittest
import os
import math

from super_browser.models import TaskSpec
from super_browser.policy import approval_required, infer_risk
from super_browser.router import build_plan, infer_task, provider_sequence_constraint_failures


class RouterPolicyTests(unittest.TestCase):
    def test_simple_extraction_prefers_playwright(self):
        task = infer_task("Extract product names from https://example.com/products")
        plan = build_plan(task)
        self.assertEqual(task.target_scope, "public_web")
        self.assertEqual(plan.council_report["planner_decision"]["target_scope"], "public_web")
        self.assertEqual(plan.mode, "direct")
        self.assertEqual(plan.primary_provider, "playwright")
        self.assertFalse(plan.approval_required)

    def test_raw_http_prefers_decodo(self):
        plan = build_plan(infer_task("Fetch this JSON endpoint through raw HTTP with a residential proxy", url="https://example.com/data.json"))
        self.assertEqual(plan.primary_provider, "decodo-http")

    def test_raw_http_without_http_url_raises(self):
        with self.assertRaisesRegex(ValueError, "Raw HTTP/API tasks require an http\\(s\\) starting URL"):
            build_plan(infer_task("Fetch this JSON endpoint through raw HTTP with a residential proxy"))

        with self.assertRaisesRegex(ValueError, "Raw HTTP/API tasks require an http\\(s\\) starting URL"):
            build_plan(infer_task("Fetch this JSON endpoint through raw HTTP", url="file:///tmp/super-browser-fixture.json"))

    def test_provider_allowlist_is_strict(self):
        plan = build_plan(infer_task("Extract titles from https://example.com", providers_allowed=["steel"]))
        self.assertEqual(plan.primary_provider, "steel")
        self.assertEqual(plan.fallback_providers, [])
        self.assertEqual(plan.council_report["planner_decision"]["providers_allowed"], ["steel"])

    def test_malformed_provider_allowlist_raises_in_core_router(self):
        with self.assertRaisesRegex(ValueError, "providers_allowed must be a list"):
            infer_task("Extract titles from https://example.com", providers_allowed="playwright")
        with self.assertRaisesRegex(ValueError, "providers_allowed entries must be strings"):
            build_plan(TaskSpec(goal="Extract titles from https://example.com", providers_allowed=[123]))

    def test_unknown_provider_allowlist_raises_in_core_router(self):
        with self.assertRaisesRegex(ValueError, "Unknown provider in providers_allowed: made-up-browser"):
            infer_task("Extract titles from https://example.com", providers_allowed=["made-up-browser"])

    def test_empty_goal_raises_in_core_router(self):
        with self.assertRaisesRegex(ValueError, "goal must be a non-empty string"):
            infer_task("   ")

    def test_invalid_url_raises_in_core_router(self):
        with self.assertRaisesRegex(ValueError, "url must include a scheme"):
            infer_task("Extract titles", url="example.com")
        with self.assertRaisesRegex(ValueError, "url must use one of these schemes"):
            infer_task("Extract titles", url="ftp://example.com/file")
        with self.assertRaisesRegex(ValueError, "url must not contain whitespace"):
            infer_task("Extract titles", url="https://example.com/raw space")
        with self.assertRaisesRegex(ValueError, "url must not contain username or password"):
            infer_task("Extract titles", url="https://agent:secret@example.com/private")
        with self.assertRaisesRegex(ValueError, "url port must be a valid integer"):
            infer_task("Extract titles", url="https://example.com:bad-port/path")
        with self.assertRaisesRegex(ValueError, "file URL must be local"):
            infer_task("Extract titles", url="file://remote-host/tmp/super-browser-fixture.html")

    def test_embedded_url_extraction_strips_common_prose_and_markdown_delimiters(self):
        cases = {
            "Extract from <https://example.com/data.json>.": "https://example.com/data.json",
            "Extract from [the endpoint](https://example.com/data.json?x=1).": "https://example.com/data.json?x=1",
            'Extract from "https://example.com/path".': "https://example.com/path",
            "Extract from https://example.com/path].": "https://example.com/path",
        }
        for goal, expected_url in cases.items():
            with self.subTest(goal=goal):
                self.assertEqual(infer_task(goal).url, expected_url)

    def test_file_url_is_allowed_for_local_browser_fixtures(self):
        task = infer_task("Extract a local browser fixture", url="file:///tmp/super-browser-fixture.html")
        self.assertEqual(task.url, "file:///tmp/super-browser-fixture.html")
        self.assertEqual(task.target_scope, "local_file")

    def test_embedded_file_url_in_goal_is_extracted_and_approval_gated(self):
        task = infer_task("Extract a local browser fixture from file:///tmp/super-browser-fixture.html")
        plan = build_plan(task)
        self.assertEqual(task.url, "file:///tmp/super-browser-fixture.html")
        self.assertEqual(task.target_scope, "local_file")
        self.assertEqual(plan.primary_provider, "playwright")
        self.assertEqual(plan.fallback_providers, [])
        self.assertEqual(plan.mode, "council")
        self.assertTrue(plan.approval_required)
        self.assertEqual(plan.council_report["approval_gate"]["reason"], "local file target requires explicit approval")

    def test_file_url_routes_only_to_local_playwright(self):
        plan = build_plan(infer_task("Extract a local browser fixture", url="file:///tmp/super-browser-fixture.html"))
        self.assertEqual(plan.primary_provider, "playwright")
        self.assertEqual(plan.fallback_providers, [])
        self.assertEqual(plan.mode, "council")
        self.assertTrue(plan.approval_required)
        self.assertEqual(plan.council_report["approval_gate"]["reason"], "local file target requires explicit approval")

    def test_file_url_rejects_non_local_provider_allowlist(self):
        task = infer_task(
            "Extract a local browser fixture",
            url="file:///tmp/super-browser-fixture.html",
            providers_allowed=["decodo-http"],
        )
        with self.assertRaisesRegex(ValueError, "No Super Saiyan Browser provider satisfies"):
            build_plan(task)

    def test_build_plan_validates_manual_task_url(self):
        with self.assertRaisesRegex(ValueError, "url must not contain username or password"):
            build_plan(TaskSpec(goal="Extract titles", url="https://agent:secret@example.com/private"))

    def test_build_plan_classifies_manual_task_target_scope(self):
        plan = build_plan(TaskSpec(goal="Fetch internal status through raw HTTP", url="http://10.0.0.5/status", raw_http=True))
        self.assertEqual(plan.task.target_scope, "private_network")
        self.assertEqual(plan.mode, "council")
        self.assertTrue(plan.approval_required)
        self.assertEqual(plan.council_report["planner_decision"]["target_scope"], "private_network")
        self.assertEqual(plan.council_report["approval_gate"]["reason"], "private-network target requires explicit approval")

    def test_loopback_target_scope_triggers_council_visibility(self):
        plan = build_plan(infer_task("Fetch this JSON endpoint through raw HTTP", url="http://127.0.0.1:8080/data.json"))
        self.assertEqual(plan.task.target_scope, "loopback")
        self.assertEqual(plan.mode, "council")
        self.assertFalse(plan.approval_required)
        self.assertIn("Target scope is loopback", " ".join(plan.rationale))

    def test_private_and_link_local_target_scopes_are_explicit(self):
        private_plan = build_plan(infer_task("Fetch this JSON endpoint through raw HTTP", url="http://intranet/api.json"))
        link_local_plan = build_plan(infer_task("Fetch this JSON endpoint through raw HTTP", url="http://169.254.169.254/latest/meta-data"))

        self.assertEqual(private_plan.task.target_scope, "private_network")
        self.assertEqual(private_plan.mode, "council")
        self.assertTrue(private_plan.approval_required)
        self.assertEqual(private_plan.council_report["planner_decision"]["target_scope"], "private_network")
        self.assertEqual(private_plan.council_report["approval_gate"]["reason"], "private-network target requires explicit approval")
        self.assertEqual(link_local_plan.task.target_scope, "link_local")
        self.assertEqual(link_local_plan.mode, "council")
        self.assertTrue(link_local_plan.approval_required)
        self.assertEqual(link_local_plan.council_report["approval_gate"]["reason"], "link-local target requires explicit approval")
        self.assertIn("Target scope is link_local", " ".join(link_local_plan.rationale))

    def test_provider_constraints_reject_stale_target_scope(self):
        plan = build_plan(infer_task("Fetch this JSON endpoint through raw HTTP", url="http://169.254.169.254/latest/meta-data"))
        plan.task.target_scope = "public_web"
        plan.approval_required = False

        failures = provider_sequence_constraint_failures(plan)

        mismatch = next(failure for failure in failures if failure["type"] == "provider_target_scope_mismatch")
        self.assertEqual(mismatch["declared_target_scope"], "public_web")
        self.assertEqual(mismatch["derived_target_scope"], "link_local")

    def test_provider_constraints_reject_primary_provider_that_requires_missing_url(self):
        plan = build_plan(infer_task("Search the web for public mentions of this brand"))
        plan.primary_provider = "steel"
        plan.fallback_providers = []

        failures = provider_sequence_constraint_failures(plan)

        missing_url = next(failure for failure in failures if failure["type"] == "provider_missing_url_constraint_violation")
        self.assertEqual(missing_url["provider"], "steel")

    def test_provider_constraints_reject_raw_http_without_http_url(self):
        plan = build_plan(infer_task("Search the web for public mentions of this brand"))
        plan.task.raw_http = True

        failures = provider_sequence_constraint_failures(plan)

        raw_http_url = next(failure for failure in failures if failure["type"] == "provider_raw_http_url_constraint_violation")
        self.assertEqual(raw_http_url["allowed_schemes"], ["http", "https"])

    def test_generated_url_less_plan_does_not_violate_provider_constraints(self):
        plan = build_plan(infer_task("Search the web for public mentions of this brand"))
        self.assertNotIn(plan.primary_provider, {"playwright", "decodo-http", "airtop", "hyperbrowser", "steel"})
        self.assertEqual(provider_sequence_constraint_failures(plan), [])

    def test_invalid_optimize_raises_in_core_router(self):
        with self.assertRaisesRegex(ValueError, "Invalid optimize value: cheapest"):
            infer_task("Extract titles from https://example.com", optimize="cheapest")
        with self.assertRaisesRegex(ValueError, "Invalid optimize value: cheapest"):
            build_plan(TaskSpec(goal="Extract titles from https://example.com", optimize="cheapest"))

    def test_invalid_max_cost_raises_in_core_router(self):
        with self.assertRaisesRegex(ValueError, "max_cost_usd must be >= 0"):
            infer_task("Extract titles from https://example.com", max_cost_usd=-0.01)
        with self.assertRaisesRegex(ValueError, "max_cost_usd must be a number"):
            infer_task("Extract titles from https://example.com", max_cost_usd=True)
        with self.assertRaisesRegex(ValueError, "max_cost_usd must be finite"):
            infer_task("Extract titles from https://example.com", max_cost_usd=math.inf)
        with self.assertRaisesRegex(ValueError, "max_cost_usd must be finite"):
            infer_task("Extract titles from https://example.com", max_cost_usd=math.nan)
        with self.assertRaisesRegex(ValueError, "max_cost_usd must be >= 0"):
            build_plan(TaskSpec(goal="Extract titles from https://example.com", max_cost_usd=-0.01))

    def test_invalid_timeout_raises_in_core_router(self):
        with self.assertRaisesRegex(ValueError, "timeout_seconds must be >= 1"):
            infer_task("Extract titles from https://example.com", timeout_seconds=0)
        with self.assertRaisesRegex(ValueError, "timeout_seconds must be a number"):
            infer_task("Extract titles from https://example.com", timeout_seconds=True)
        with self.assertRaisesRegex(ValueError, "timeout_seconds must be finite"):
            infer_task("Extract titles from https://example.com", timeout_seconds=math.inf)
        with self.assertRaisesRegex(ValueError, "timeout_seconds must be an integer"):
            infer_task("Extract titles from https://example.com", timeout_seconds=1.5)
        with self.assertRaisesRegex(ValueError, "timeout_seconds must be >= 1"):
            build_plan(TaskSpec(goal="Extract titles from https://example.com", timeout_seconds=0))

    def test_timeout_is_visible_in_plan(self):
        plan = build_plan(infer_task("Extract titles from https://example.com", timeout_seconds=45))
        self.assertEqual(plan.task.timeout_seconds, 45)
        self.assertEqual(plan.council_report["planner_decision"]["timeout_seconds"], 45)
        self.assertIn("timeout_seconds=45", " ".join(plan.rationale))

    def test_max_cost_filters_expensive_providers(self):
        plan = build_plan(infer_task("Search Facebook Ads Library behind Cloudflare and extract advertisers", url="https://example.com", max_cost_usd=0.0))
        self.assertEqual(plan.primary_provider, "playwright")
        self.assertEqual(plan.council_report["planner_decision"]["max_cost_usd"], 0.0)
        self.assertEqual(plan.council_report["planner_decision"]["estimated_cost_floor_usd"], 0.0)
        self.assertEqual(plan.cost_estimate["budget_status"], "within_ceiling")
        self.assertEqual(plan.cost_estimate["selected_provider_floor_usd"], 0.0)

    def test_impossible_provider_constraints_raise(self):
        task = infer_task("Use a desktop computer to inspect files", providers_allowed=["orgo"], max_cost_usd=0.0)
        with self.assertRaisesRegex(ValueError, "No Super Saiyan Browser provider satisfies"):
            build_plan(task)

    def test_desktop_prefers_orgo(self):
        saved = {
            name: os.environ.pop(name, None)
            for name in ("ORGO_API_KEY", "ORGO_COMPUTER_ID", "BROWSER_USE_API_KEY")
        }
        try:
            plan = build_plan(infer_task("Open a full desktop VM and combine local CSV files in a spreadsheet app"))
            self.assertEqual(plan.mode, "council")
            self.assertEqual(plan.primary_provider, "orgo")
            self.assertIn("ORGO_API_KEY", plan.missing_env)
            self.assertNotIn("ORGO_COMPUTER_ID", plan.missing_env)
            orgo_steps = [step for step in plan.steps if step.provider == "orgo"]
            self.assertIn("ORGO_API_KEY", orgo_steps[0].required_env)
            self.assertNotIn("ORGO_COMPUTER_ID", orgo_steps[0].required_env)
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_auth_prefers_browser_use(self):
        plan = build_plan(infer_task("Use my logged in Chrome session to read private dashboard notifications"))
        self.assertEqual(plan.mode, "council")
        self.assertEqual(plan.primary_provider, "browser-use")
        safety_steps = [step for step in plan.steps if step.provider == "publishing-safety-specialist"]
        self.assertEqual(safety_steps[0].risk, "credential")

    def test_authenticated_browser_profile_requires_credential_approval(self):
        task = infer_task("Use my authenticated Chrome profile to read dashboard alerts")
        plan = build_plan(task)
        self.assertEqual(infer_risk(task.goal), "credential")
        self.assertTrue(task.requires_auth)
        self.assertTrue(approval_required(task))
        self.assertTrue(plan.approval_required)
        self.assertEqual(plan.primary_provider, "browser-use")
        self.assertEqual(plan.mode, "council")

    def test_public_profile_read_does_not_require_auth_or_approval(self):
        task = infer_task("Extract public profile headline and visible posts from this page")
        plan = build_plan(task)
        self.assertEqual(infer_risk(task.goal), "read")
        self.assertFalse(task.requires_auth)
        self.assertFalse(task.external_write)
        self.assertFalse(approval_required(task))
        self.assertFalse(plan.approval_required)
        self.assertEqual(plan.mode, "direct")

    def test_anti_bot_prefers_browser_use(self):
        plan = build_plan(infer_task("Search Facebook Ads Library behind Cloudflare and extract advertisers"))
        self.assertEqual(plan.mode, "council")
        self.assertEqual(plan.primary_provider, "browser-use")
        self.assertFalse(plan.approval_required)
        report = plan.council_report
        self.assertEqual(report["mode"], "council")
        self.assertGreaterEqual(len(report["review_loops"]), 3)
        self.assertTrue(report.get("deliberation_complete", True))
        browser_use = [item for item in report["specialists_consulted"] if item["provider"] == "browser-use"][0]
        self.assertEqual(browser_use["recommendation"], "use me")
        self.assertIn("BROWSER_USE_API_KEY", browser_use["required_env"])
        self.assertEqual(report["planner_decision"]["primary_provider"], "browser-use")
        self.assertEqual(report["cost_estimate"]["primary"]["provider"], "browser-use")
        self.assertEqual(report["cost_estimate"]["primary"]["confidence"], "low")
        self.assertGreater(report["cost_estimate"]["worst_case_floor_usd"], report["cost_estimate"]["selected_provider_floor_usd"])

    def test_facebook_read_only_extraction_does_not_match_book_action(self):
        task = infer_task("Search Facebook Ads Library behind Cloudflare and extract advertisers")
        self.assertEqual(infer_risk(task.goal), "read")
        self.assertFalse(task.external_write)
        self.assertFalse(approval_required(task))

    def test_long_running_cost_estimate_has_multiplier_notes(self):
        plan = build_plan(infer_task("Monitor this public page every day for changes", url="https://example.com"))
        estimate = plan.cost_estimate["primary"]
        self.assertTrue(plan.task.long_running)
        self.assertIsNone(plan.council_report["planner_decision"]["timeout_seconds"])
        self.assertEqual(plan.mode, "council")
        self.assertEqual(estimate["provider"], plan.primary_provider)
        self.assertGreaterEqual(estimate["multiplier"], 3.0)
        self.assertIn("Long-running workflow", " ".join(estimate["notes"]))

    def test_direct_plan_still_has_council_report(self):
        plan = build_plan(infer_task("Extract titles from https://example.com"))
        report = plan.council_report
        self.assertEqual(report["mode"], "direct")
        self.assertGreaterEqual(len(report["review_loops"]), 3)
        playwright = [item for item in report["specialists_consulted"] if item["provider"] == "playwright"][0]
        self.assertEqual(playwright["recommendation"], "use me")

    def test_external_write_requires_approval(self):
        task = infer_task("Draft a LinkedIn comment and post it")
        plan = build_plan(task)
        self.assertTrue(approval_required(task))
        self.assertTrue(plan.approval_required)
        self.assertEqual(infer_risk(task.goal), "external_write")
        self.assertIn("publishing-safety-specialist", [step.provider for step in plan.steps])
        self.assertTrue(plan.council_report["approval_gate"]["required"])
        self.assertEqual(plan.council_report["approval_gate"]["specialist"], "publishing-safety-specialist")

    def test_url_less_external_write_avoids_url_required_primary_provider(self):
        plan = build_plan(infer_task("Send a message in the browser"))
        self.assertTrue(plan.approval_required)
        self.assertNotIn(plan.primary_provider, {"playwright", "decodo-http", "airtop", "hyperbrowser", "steel"})

    def test_social_and_lead_gen_actions_require_approval(self):
        goals = [
            "Like the latest LinkedIn post",
            "Follow this Instagram profile",
            "Join this Facebook group",
            "Accept this LinkedIn connection request",
            "Book a demo appointment from this website",
            "Request info from this vendor page",
            "Request a demo from this website",
            "Request pricing from this supplier",
            "Click request info on this vendor page",
            "Apply to this job posting",
            "Subscribe to this newsletter",
            "RSVP yes to this event",
            "Upvote this Reddit post",
            "Downvote this forum answer",
            "Star this GitHub repository",
            "Watch repo updates on GitHub",
            "Fork this GitHub repository",
            "Bookmark this post",
            "Save this Instagram post",
            "Pin this post to the board",
            "Report this profile",
            "Block this user",
            "Mute this thread",
            "Respond to this DM",
            "Decline this connection request",
            "Remove this LinkedIn connection",
            "Tag this lead in the CRM",
            "Mark this lead as contacted in the CRM",
            "Move this lead to contacted stage",
            "Add this lead to the campaign list",
            "Quote tweet this post with my response",
            "Share this reel to my story",
            "Create a Facebook group for this campaign",
            "Create an event for this webinar",
            "Create a page for this brand",
            "Send a connection request to this prospect",
            "Add this person as a friend",
            "Remove this follower from my account",
            "Unfriend this user",
            "Cancel this follow request",
            "Confirm this friend request",
            "Approve this group request",
            "Write a review for this vendor",
            "Leave a review on this listing",
            "Rate this app five stars",
            "Vote yes in this poll",
            "Clap for this Medium article",
            "Add this item to favorites",
            "Mark interested in this event",
            "Boost post visibility",
            "Promote post on the platform",
            "Create ad for this campaign",
            "Launch ad for this page",
            "Run ad using this audience",
            "Donate to this fundraiser",
            "Bid on this auction item",
            "Checkout with this cart",
            "Click the publish button on this draft",
            "Tap the heart icon on this Instagram post",
            "Press enter to send this DM",
            "Hit the submit button on this lead form",
            "Activate the follow control on this profile",
            "Browse this public Facebook group and click the like button on a post",
            "Browse public group posts, then hit the submit button on the lead form",
            "Email this lead with the approved intro",
            "Email jane@example.com with the follow-up details",
            "Email these leads with the approved intro",
            "Email prospects from this campaign",
            "Email selected warm leads from the saved list",
            "Edit profile headline",
            "Update bio text",
            "Change username",
            "Change email on the account",
            "Change profile picture",
            "Set status to available",
            "Create account on this site",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "external_write")
                self.assertTrue(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

    def test_external_state_change_actions_require_approval(self):
        goals = [
            "Accept the group invitation",
            "Accept this calendar invite",
            "Decline this event invitation",
            "Connect to this prospect on LinkedIn",
            "Mark myself as going to this event",
            "Mark this event as going",
            "Click attend on the event page",
            "Click going on the event page",
            "Turn on notifications for this profile",
            "Turn off notifications for this thread",
            "Snooze notifications for this thread",
            "Archive this email thread",
            "Mark this email as read",
            "Move this message to the archive",
            "Add this product to cart",
            "Add this person to CRM",
            "Create a lead in Salesforce",
            "Assign this lead to me",
            "Enroll this lead in the campaign",
            "Remove this member from the group",
            "Turn off comments on this post",
            "Hide this post from timeline",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "external_write")
                self.assertTrue(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

    def test_additional_social_calendar_and_saved_state_actions_require_approval(self):
        goals = [
            "Unlike the latest LinkedIn post",
            "Remove my reaction from this Facebook post",
            "Unsave this Instagram post",
            "Remove this post from saved items",
            "Unbookmark this profile",
            "Unfavorite this item",
            "Stop watching this GitHub repository",
            "Unstar this GitHub repository",
            "Mute this profile",
            "Report this comment",
            "Hide this comment",
            "Trash this Google Drive file",
            "Restore this file from trash",
            "Cancel this calendar event",
            "Reschedule this meeting",
            "Cancel this scheduled post",
            "Unschedule this post",
            "Remove this lead from the campaign list",
            "Remove this contact from HubSpot",
            "Unenroll this lead from the campaign sequence",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "external_write")
                self.assertTrue(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

    def test_destructive_account_actions_require_approval(self):
        goals = [
            "Deactivate account on this platform",
            "Disable account access",
            "Close account permanently",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "destructive")
                self.assertTrue(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

    def test_account_and_private_wording_requires_credential_approval(self):
        task = infer_task("Use my account to read private dashboard notifications")
        plan = build_plan(task)
        self.assertEqual(infer_risk(task.goal), "credential")
        self.assertTrue(task.requires_auth)
        self.assertTrue(approval_required(task))
        self.assertTrue(plan.approval_required)

    def test_private_message_read_requires_credential_approval(self):
        task = infer_task("Read my LinkedIn messages and summarize them")
        plan = build_plan(task)
        self.assertEqual(infer_risk(task.goal), "credential")
        self.assertTrue(task.requires_auth)
        self.assertFalse(task.external_write)
        self.assertTrue(approval_required(task))
        self.assertTrue(plan.approval_required)

    def test_accounting_text_does_not_trigger_credential_policy(self):
        task = infer_task("Extract accounting firm names from this public directory")
        plan = build_plan(task)
        self.assertEqual(infer_risk(task.goal), "read")
        self.assertFalse(task.requires_auth)
        self.assertFalse(approval_required(task))
        self.assertFalse(plan.approval_required)

    def test_router_term_matching_does_not_match_inside_words(self):
        task = infer_task("Extract metadata for privateer ship profiles from a public archive")
        plan = build_plan(task)
        self.assertEqual(infer_risk(task.goal), "read")
        self.assertFalse(task.anti_bot_risk)
        self.assertFalse(task.requires_auth)
        self.assertEqual(plan.mode, "direct")
        self.assertFalse(plan.approval_required)

    def test_platform_write_terms_do_not_match_read_only_lookalikes(self):
        goals = [
            "Extract 5-star review counts from this public page",
            "Watch the public product video and summarize the visible captions",
            "Report the current page title and status text",
            "Save screenshots locally for the run report",
            "Extract pinned posts from this public profile",
            "Extract email addresses from this public directory",
            "Find the visible email on this public contact page",
            "Find email leads in this public directory",
            "Extract visible comments from this public LinkedIn post",
            "Extract message text from this public forum thread",
            "Browse this public Facebook group and find posts about roofing leads",
            "Extract request info copy from this public landing page",
            "Find lead stage names from this public CRM help article",
            "List campaign naming examples from this public page",
            "Send me a summary of this public page",
            "Send me a summary of this public message board",
            "Extract notification settings documentation from this public help page",
            "Find CRM lead assignment examples from this public documentation",
            "List shopping cart icon labels from this public style guide",
            "Summarize event attendance policy from this public page",
            "Extract invitation templates from this public page",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "read")
                self.assertFalse(task.external_write)
                self.assertFalse(approval_required(task))
                self.assertFalse(plan.approval_required)

    def test_business_content_phrases_do_not_trigger_external_write(self):
        goals = [
            "Create a lead magnet outline for my website",
            "Draft lead magnet copy but do not publish",
            "Draft invite copy for an event but do not send",
            "Create an invite template but do not send",
            "Create a posting schedule outline",
            "Apply a filter to the public search results and extract names",
            "Book notes from this public page into a summary",
            "Write a review summary of this product page",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertNotEqual(infer_risk(task.goal), "external_write")
                self.assertFalse(task.external_write)
                self.assertFalse(approval_required(task))
                self.assertFalse(plan.approval_required)

    def test_contextual_external_state_actions_still_require_approval(self):
        goals = [
            "Create a lead in Salesforce",
            "Invite this user to the workspace",
            "Schedule a meeting with this prospect",
            "Book an appointment for this customer",
            "Apply for this job",
            "Write a review for this product",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "external_write")
                self.assertTrue(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

    def test_local_lead_and_contact_outputs_do_not_trigger_external_write(self):
        goals = [
            "Create a lead list locally from public search results",
            "Create a lead in the local output file, not in CRM",
            "Create a contact list locally from this public directory",
            "Create a local contact record JSON file from extracted public info",
            "Add extracted leads to a local CSV file only",
            "Write extracted contacts to a local output file",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertNotEqual(infer_risk(task.goal), "external_write")
                self.assertFalse(task.external_write)
                self.assertFalse(approval_required(task))
                self.assertFalse(plan.approval_required)

    def test_crm_lead_and_contact_writes_still_require_approval(self):
        goals = [
            "Create a lead in Salesforce",
            "Create a contact in HubSpot",
            "Create a customer record in CRM",
            "Add extracted leads to the Salesforce campaign",
            "Write extracted contacts to HubSpot",
            "Create a lead in CRM and save a local copy",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "external_write")
                self.assertTrue(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

    def test_public_search_and_filter_submissions_do_not_trigger_external_write(self):
        goals = [
            "Submit the public search form and extract results",
            "Submit the site search for roofing posts and extract results",
            "Press enter to submit the search query and list public posts",
            "Submit the filter form to sort public results by newest",
            "Submit the sort form and extract visible public listings",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "read")
                self.assertFalse(task.external_write)
                self.assertFalse(approval_required(task))
                self.assertFalse(plan.approval_required)

    def test_safe_search_submission_does_not_mask_followup_external_write(self):
        goals = [
            "Submit the public search form, extract results, and like the latest post",
            "Submit the public search form and save the first post",
            "Submit the filter form and bookmark this result",
            "Submit the sort form and share the newest listing",
            "Press enter to submit the search query and upvote the top comment",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "external_write")
                self.assertTrue(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

    def test_real_form_submissions_still_require_approval(self):
        goals = [
            "Submit the lead form on this site",
            "Submit the contact form with this message",
            "Submit the application form for this job",
            "Submit the checkout form",
            "Submit the signup form",
            "Submit a comment form",
            "Submit a search form that contains my private address",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertIn(infer_risk(task.goal), {"external_write", "credential"})
                self.assertTrue(task.external_write or task.requires_auth)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

    def test_ecommerce_cart_bag_and_wishlist_actions_require_approval(self):
        goals = [
            "Add this product to the cart",
            "Add the first product to the shopping cart",
            "Add this item to the basket",
            "Add this item to bag",
            "Add this item to the bag",
            "Add this item to wishlist",
            "Add this item to the wishlist",
            "Add this product to the waitlist",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "external_write")
                self.assertTrue(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

    def test_cart_and_wishlist_read_only_lookalikes_remain_read_only(self):
        goals = [
            "Add the visible products to a local CSV list",
            "Extract add-to-cart button labels from this public page",
            "Find wishlist UX examples from this public page",
            "Save this product screenshot locally",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "read")
                self.assertFalse(task.external_write)
                self.assertFalse(approval_required(task))
                self.assertFalse(plan.approval_required)

    def test_ecommerce_order_and_payment_actions_require_approval(self):
        goals = [
            "Remove this item from the cart",
            "Remove this item from the bag",
            "Change quantity to 2 in the cart",
            "Set delivery address to the saved office address",
            "Apply promo code SAVE10 to this cart",
            "Redeem this coupon",
            "Claim this offer",
            "Place the order",
            "Pay this invoice",
            "Pay the bill on this portal",
            "Preorder this product",
            "Pre-order this product",
            "Add this product to the wish list",
            "Request a refund for this order",
            "Request a return for this order",
            "Cancel this order",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "external_write")
                self.assertTrue(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

    def test_order_and_payment_read_only_lookalikes_remain_read_only(self):
        goals = [
            "Extract remove-from-cart button labels from this public page",
            "Find coupon code examples from this public blog post",
            "List refund policy details from this public page",
            "Summarize shipping address form UX from this public page",
            "Extract order status text from this public help article",
            "Create local notes about invoice payment UX from this public page",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "read")
                self.assertFalse(task.external_write)
                self.assertFalse(approval_required(task))
                self.assertFalse(plan.approval_required)

    def test_project_management_and_repo_actions_require_approval(self):
        goals = [
            "Create a GitHub issue for this bug",
            "Open a GitHub issue for this bug",
            "Close this GitHub issue",
            "Reopen this issue",
            "Merge this pull request",
            "Create a pull request from this branch",
            "Open a pull request from this branch",
            "Request review on this pull request",
            "Add label bug to this issue",
            "Assign this issue to me",
            "Move this Trello card to Done",
            "Create a Jira ticket for this bug",
            "Close this Jira ticket",
            "Update the status of this ticket to Done",
            "Create a GitHub repository for this project",
            "Archive this GitHub repository",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "external_write")
                self.assertTrue(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

    def test_project_management_read_only_lookalikes_remain_read_only(self):
        goals = [
            "List issue labels from this public GitHub page",
            "Summarize PR merge policy from this public docs page",
            "Create local notes about GitHub issue templates",
            "Extract Jira status examples from this public article",
            "Find Trello card layout examples from this public blog post",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "read")
                self.assertFalse(task.external_write)
                self.assertFalse(approval_required(task))
                self.assertFalse(plan.approval_required)

    def test_cloud_document_and_integration_actions_require_approval(self):
        goals = [
            "Create a Google Drive folder for this client",
            "Create a folder in Dropbox for this project",
            "Rename this Google Doc",
            "Move this file to the shared folder",
            "Copy this file into the client folder",
            "Make this document public",
            "Grant editor access to this file",
            "Revoke access for this user",
            "Remove access for this user",
            "Install this Slack app",
            "Authorize this integration",
            "Connect this app to Google Calendar",
            "Grant calendar access to this app",
            "Change notification preferences",
            "Save settings on this account page",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "external_write")
                self.assertTrue(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

    def test_cloud_document_and_auth_reference_lookalikes_remain_read_only(self):
        goals = [
            "List Google Drive folder naming examples from this public page",
            "Summarize document sharing policy from this public help article",
            "Create local notes about OAuth consent screens",
            "Extract app integration setup steps from this public documentation",
            "Find token storage best practices from this public security guide",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "read")
                self.assertFalse(task.requires_auth)
                self.assertFalse(task.external_write)
                self.assertFalse(approval_required(task))
                self.assertFalse(plan.approval_required)

    def test_secrets_infrastructure_and_billing_actions_require_approval(self):
        external_write_goals = [
            "Generate a new API key",
            "Create an API key for this app",
            "Rotate this API key",
            "Revoke this API key",
            "Create a webhook for this project",
            "Update the webhook URL",
            "Create a Vercel deployment",
            "Deploy this site to production",
            "Roll back this deployment",
            "Create a DNS record",
            "Update the MX record",
            "Change the domain nameservers",
            "Create an environment variable in Vercel",
            "Update this production env var",
            "Start a free trial",
            "Upgrade this billing plan",
            "Downgrade this subscription",
            "Add a payment method",
        ]
        for goal in external_write_goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "external_write")
                self.assertTrue(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

        credential_goals = [
            "Reveal this API key",
            "Copy this API key to the clipboard",
        ]
        for goal in credential_goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "credential")
                self.assertTrue(task.requires_auth)
                self.assertFalse(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

    def test_secrets_infrastructure_and_billing_reference_lookalikes_remain_read_only(self):
        goals = [
            "List API key rotation best practices from this public docs page",
            "Summarize webhook setup docs from this public guide",
            "Find DNS record examples from this public article",
            "Create local notes about environment variable naming",
            "Extract billing plan comparison text from this public pricing page",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "read")
                self.assertFalse(task.requires_auth)
                self.assertFalse(task.external_write)
                self.assertFalse(approval_required(task))
                self.assertFalse(plan.approval_required)

    def test_financial_trading_and_money_movement_actions_require_approval(self):
        goals = [
            "Sell 10 shares of AAPL",
            "Place a market order for AAPL",
            "Place a limit order for BTC",
            "Open a long position on ETH",
            "Close this options position",
            "Liquidate this crypto position",
            "Buy 0.1 ETH",
            "Swap USDC for ETH",
            "Stake this SOL",
            "Unstake this SOL",
            "Withdraw $500 to my bank account",
            "Deposit $500 into this brokerage account",
            "Transfer funds to this recipient",
            "Send this wire transfer",
            "Pay this vendor via ACH",
            "Add this bank account as a payout method",
            "Update the payout account",
            "Connect this bank account",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "external_write")
                self.assertTrue(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

    def test_financial_trading_and_money_movement_reference_lookalikes_remain_read_only(self):
        goals = [
            "List stock trade examples from this public help article",
            "Summarize crypto staking risks from this public docs page",
            "Create local notes about ACH transfer wording",
            "Extract brokerage order type descriptions from this public guide",
            "Find bank account linking examples from this public article",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "read")
                self.assertFalse(task.requires_auth)
                self.assertFalse(task.external_write)
                self.assertFalse(approval_required(task))
                self.assertFalse(plan.approval_required)

    def test_regulated_legal_government_health_and_identity_actions_require_approval(self):
        goals = [
            "Sign this contract",
            "E-sign this NDA",
            "Certify this legal form",
            "Attest to this disclosure",
            "File my tax return",
            "File this court document",
            "Submit this insurance claim",
            "Update this insurance coverage",
            "Cancel this insurance policy",
            "Enroll me in this health plan",
            "Change my benefits election",
            "Refill this prescription",
            "Order this prescription refill",
            "Send this medical form to the clinic",
            "Upload this medical record",
            "Renew my passport",
            "Apply for a visa",
            "Register me to vote",
            "Change my mailing address with the DMV",
            "Update my emergency contact",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "external_write")
                self.assertTrue(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

    def test_regulated_reference_lookalikes_remain_read_only(self):
        goals = [
            "List contract signature UX examples from this public article",
            "Summarize tax filing documentation from this public IRS guide",
            "Create local notes about insurance claim wording",
            "Extract prescription refill policy from this public help page",
            "Find passport renewal checklist from this public government page",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "read")
                self.assertFalse(task.requires_auth)
                self.assertFalse(task.external_write)
                self.assertFalse(approval_required(task))
                self.assertFalse(plan.approval_required)

    def test_workspace_channel_and_moderation_actions_require_approval(self):
        goals = [
            "Create a Slack channel for this project",
            "Rename this Slack channel",
            "Archive this Slack channel",
            "Unarchive this Slack channel",
            "Add this user to the Slack channel",
            "Remove this user from the Slack channel",
            "Kick this user from the Discord server",
            "Ban this user from the Discord server",
            "Unban this user from the Discord server",
            "Make this user an admin",
            "Promote this member to admin",
            "Demote this admin to member",
            "Change this user's role to moderator",
            "Pin this Slack message",
            "Unpin this Slack message",
            "Create a Notion workspace",
            "Rename this workspace",
            "Archive this Notion page",
            "Lock this thread",
            "Unlock this thread",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "external_write")
                self.assertTrue(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

    def test_workspace_channel_and_moderation_reference_lookalikes_remain_read_only(self):
        goals = [
            "List Slack channel naming examples from this public docs page",
            "Summarize Discord moderation policy from this public guide",
            "Create local notes about Slack channel roles",
            "Extract workspace admin role examples from this public help article",
            "Find thread lock UX examples from this public article",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "read")
                self.assertFalse(task.requires_auth)
                self.assertFalse(task.external_write)
                self.assertFalse(approval_required(task))
                self.assertFalse(plan.approval_required)

    def test_read_only_lookup_prefix_does_not_mask_followup_external_state_changes(self):
        goals = [
            "Browse this public Facebook group posts and connect to this prospect",
            "Read visible comments and archive this email thread",
            "List public posts and add this person to CRM",
            "Find public posts and create a lead in Salesforce",
            "Browse public group posts and mark this lead as contacted",
            "Extract public comments and turn on notifications for this profile",
            "Review public messages and mark this email as read",
            "Search public posts and add this product to cart",
            "Browse public posts and remove this member from the group",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "external_write")
                self.assertTrue(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

    def test_follow_up_text_does_not_match_follow_action(self):
        draft_task = infer_task("Draft a follow-up email but do not send")
        draft_plan = build_plan(draft_task)
        self.assertEqual(infer_risk(draft_task.goal), "mutating")
        self.assertTrue(draft_task.draft_only)
        self.assertFalse(draft_task.external_write)
        self.assertFalse(approval_required(draft_task))
        self.assertFalse(draft_plan.approval_required)

        read_task = infer_task("Extract follow-up email templates from this public page")
        read_plan = build_plan(read_task)
        self.assertEqual(infer_risk(read_task.goal), "read")
        self.assertFalse(read_task.draft_only)
        self.assertFalse(read_task.external_write)
        self.assertFalse(approval_required(read_task))
        self.assertFalse(read_plan.approval_required)

    def test_local_delivery_request_with_followup_external_write_requires_approval(self):
        goals = [
            "Send me a summary of this public page and email this lead with the approved intro",
            "Send me the findings, then post a LinkedIn comment",
            "Send us the report and submit the lead form",
            "Send me a summary, then press enter to send this DM",
            "Send me a list of public posts and like the latest LinkedIn post",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "external_write")
                self.assertTrue(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)

    def test_credential_external_write_sets_both_policy_flags(self):
        task = infer_task("Use my logged in Chrome session to post a LinkedIn comment")
        plan = build_plan(task)
        self.assertEqual(infer_risk(task.goal), "external_write")
        self.assertTrue(task.requires_auth)
        self.assertTrue(task.external_write)
        self.assertTrue(approval_required(task))
        self.assertTrue(plan.approval_required)
        self.assertEqual(plan.council_report["approval_gate"]["reason"], "external write, destructive, or credential-bearing workflow")

    def test_draft_only_comment_does_not_require_approval(self):
        task = infer_task("Draft a LinkedIn comment, put it in the box, but do not publish")
        plan = build_plan(task)
        self.assertEqual(infer_risk(task.goal), "mutating")
        self.assertTrue(task.draft_only)
        self.assertFalse(task.external_write)
        self.assertFalse(approval_required(task))
        self.assertFalse(plan.approval_required)
        self.assertNotIn("publishing-safety-specialist", [step.provider for step in plan.steps])
        self.assertEqual(
            plan.council_report["approval_gate"]["reason"],
            "draft-only workflow; publishing/posting/commenting/replying/messaging/sending/submitting remains disallowed",
        )

    def test_upload_still_requires_approval_even_with_no_submit_language(self):
        task = infer_task("Upload this file to the portal but do not submit the final form")
        plan = build_plan(task)
        self.assertEqual(infer_risk(task.goal), "external_write")
        self.assertFalse(task.draft_only)
        self.assertTrue(approval_required(task))
        self.assertTrue(plan.approval_required)

    def test_credential_draft_still_requires_approval(self):
        task = infer_task("Use my logged in session to draft a DM but do not send")
        plan = build_plan(task)
        self.assertEqual(infer_risk(task.goal), "credential")
        self.assertTrue(task.requires_auth)
        self.assertTrue(approval_required(task))
        self.assertTrue(plan.approval_required)

    def test_reply_draft_only_can_stop_before_send(self):
        task = infer_task("Write a reply in the message box but do not send")
        plan = build_plan(task)
        self.assertEqual(infer_risk(task.goal), "mutating")
        self.assertTrue(task.draft_only)
        self.assertFalse(task.external_write)
        self.assertFalse(approval_required(task))
        self.assertFalse(plan.approval_required)

    def test_email_draft_only_can_stop_before_send(self):
        task = infer_task("Draft an email to this lead but do not send")
        plan = build_plan(task)
        self.assertEqual(infer_risk(task.goal), "mutating")
        self.assertTrue(task.draft_only)
        self.assertFalse(task.external_write)
        self.assertFalse(approval_required(task))
        self.assertFalse(plan.approval_required)

    def test_form_fill_stop_before_submit_is_draft_only(self):
        goals = [
            "Fill in the contact form but stop before submitting",
            "Fill out this lead form and stop before submit",
            "Type a reply in the box and stop before sending",
            "Draft a LinkedIn comment but do not comment",
            "Prepare a public reply but do not reply",
            "Write a response in the text box without responding",
            "Draft a DM but stop before DMing",
            "Type a message in the compose box but do not message",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "mutating")
                self.assertTrue(task.draft_only)
                self.assertFalse(task.external_write)
                self.assertFalse(approval_required(task))
                self.assertFalse(plan.approval_required)

    def test_group_browsing_with_actual_comment_requires_approval(self):
        task = infer_task("Find posts in this group and comment intelligently")
        plan = build_plan(task)
        self.assertEqual(infer_risk(task.goal), "external_write")
        self.assertTrue(task.external_write)
        self.assertTrue(approval_required(task))
        self.assertTrue(plan.approval_required)

    def test_non_draftable_social_actions_ignore_no_publish_language(self):
        goals = [
            "Follow this profile but do not publish anything",
            "Like this post but do not comment",
            "Upload this file but do not send",
        ]
        for goal in goals:
            with self.subTest(goal=goal):
                task = infer_task(goal)
                plan = build_plan(task)
                self.assertEqual(infer_risk(task.goal), "external_write")
                self.assertFalse(task.draft_only)
                self.assertTrue(task.external_write)
                self.assertTrue(approval_required(task))
                self.assertTrue(plan.approval_required)


if __name__ == "__main__":
    unittest.main()
