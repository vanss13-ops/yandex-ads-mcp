#!/usr/bin/env python3
"""Offline smoke tests for the fork's safety/access additions.

No network and no credentials required. Verifies:
  - mutating vs read-only tool classification
  - Direct vs Metrika/Wordstat classification
  - YD_LOG_FILE no longer writes a log file by default
  - partial-success annotation of Direct responses
  - tool schemas expose client_login (and confirm when YD_CONFIRM=on)

Run: python3 test_safety.py
"""
import os
import sys
import asyncio

# Token must be present for the module to import cleanly under some setups.
os.environ.setdefault("YD_OAUTH_TOKEN", "test-token")
os.environ["YD_CONFIRM"] = "true"  # so schemas advertise the confirm flag
# The fallback checks below assume per-service tokens are NOT set.
os.environ.pop("YD_METRIKA_TOKEN", None)
os.environ.pop("YD_AUDIENCE_TOKEN", None)
# Extra Direct cabinets: one valid entry, one malformed (must be skipped).
os.environ["YD_DIRECT_TOKENS"] = "e-2:tok-two, broken-entry ,:no-login"

import server  # noqa: E402
from tools_direct_extra import annotate_partial  # noqa: E402

failures = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        failures.append(name)


print("== mutating classification ==")
MUTATING = [
    "yd_campaigns_add", "yd_campaigns_update", "yd_campaigns_action",
    "yd_unified_campaigns_add", "yd_unified_adgroups_add", "yd_responsive_ads_add",
    "yd_ads_update", "yd_keyword_bids_set_auto", "yd_bid_modifiers_toggle",
    "yd_callouts_link", "yd_metrika_label_link", "yd_metrika_goal_delete",
    "yd_metrika_grant_add", "yd_metrika_upload_conversions", "yd_videos_upload",
    "yd_excluded_sites_update", "yd_blocked_ips_update", "yd_campaign_strategy_update",
    "yd_keywords_suspend", "yd_keywords_resume", "yd_keywords_delete",
    "yd_audience_targets_suspend", "yd_audience_targets_resume",
    "yd_retargeting_lists_update",
    "yd_audience_segment_upload", "yd_audience_segment_confirm",
    "yd_audience_segment_reprocess", "yd_audience_segment_create_lookalike",
    "yd_audience_grant_add", "yd_audience_grant_delete",
    "yd_audience_pixel_undelete", "yd_audience_delegate_add",
]
READONLY = [
    "yd_campaigns_get", "yd_keywords_research", "yd_keywords_has_volume",
    "yd_report", "yd_changes_check", "yd_wordstat_top_requests",
    "yd_wordstat_regions_tree", "yd_metrika_report", "yd_metrika_report_comparison",
    "yd_metrika_counters_get", "yd_metrika_conversions_status",
    "yd_excluded_sites_get", "yd_regions_get", "yd_interests_get",
    "yd_changes_timestamp_get",
    "yd_audience_segments_get", "yd_audience_pixels_get",
    "yd_audience_grants_get", "yd_audience_accounts_get", "yd_audience_delegates_get",
]
for n in MUTATING:
    check(f"{n} is mutating", server._is_mutating(n) is True)
for n in READONLY:
    check(f"{n} is read-only", server._is_mutating(n) is False)

print("== direct vs metrika/wordstat/audience ==")
check("yd_campaigns_add is direct", server._is_direct("yd_campaigns_add") is True)
check("yd_vcards_add is direct", server._is_direct("yd_vcards_add") is True)
check("yd_metrika_report not direct", server._is_direct("yd_metrika_report") is False)
check("yd_wordstat_top_requests not direct", server._is_direct("yd_wordstat_top_requests") is False)
check("yd_audience_targets_add is direct (Direct API AudienceTargets)",
      server._is_direct("yd_audience_targets_add") is True)
check("yd_audience_segments_get not direct (Audience API)",
      server._is_direct("yd_audience_segments_get") is False)

print("== per-service token fallback ==")
check("METRIKA_TOKEN falls back to TOKEN", server.METRIKA_TOKEN == server.TOKEN)
check("AUDIENCE_TOKEN falls back to TOKEN", server.AUDIENCE_TOKEN == server.TOKEN)

print("== per-login Direct tokens (YD_DIRECT_TOKENS) ==")
from tools_direct_extra import client_login_var, resolve_direct_auth  # noqa: E402
check("token map parsed, malformed entries skipped", server.DIRECT_TOKENS == {"e-2": "tok-two"})
check("no login -> default token, no Client-Login",
      resolve_direct_auth(server.TOKEN, "") == (server.TOKEN, ""))
_ctx = client_login_var.set("e-2")
try:
    check("mapped login -> its own token, no Client-Login",
          resolve_direct_auth(server.TOKEN, "") == ("tok-two", ""))
    check("mapped login: server._headers() has no Client-Login",
          "Client-Login" not in server._headers() and server._headers()["Authorization"] == "Bearer tok-two")
finally:
    client_login_var.reset(_ctx)
_ctx = client_login_var.set("agency-sub")
try:
    check("unmapped login -> default token + Client-Login (agency path)",
          resolve_direct_auth(server.TOKEN, "") == (server.TOKEN, "agency-sub"))
finally:
    client_login_var.reset(_ctx)
check("yd_direct_accounts_get is read-only", server._is_mutating("yd_direct_accounts_get") is False)

print("== logging default ==")
check("no log file written by default", server.LOG_FILE == "" and
      not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(server.__file__)), "yandex-ads.log")))

print("== partial-success annotation ==")
ok = annotate_partial({"result": {"AddResults": [{"Id": 1}]}})
check("clean result has no _partial_success", "_partial_success" not in ok)
bad = annotate_partial({"result": {"AddResults": [
    {"Id": 1},
    {"Errors": [{"Code": 5, "Message": "Bad text"}]},
    {"Warnings": [{"Code": 9, "Message": "Truncated"}]},
]}})
ps = bad.get("_partial_success", {})
check("errors detected", ps.get("error_count") == 1)
check("warnings detected", ps.get("warning_count") == 1)
check("ok flag false on error", ps.get("ok") is False)

print("== schema augmentation ==")
tools = {t.name: t for t in __import__("asyncio").run(server.list_tools())}
add_props = tools["yd_campaigns_add"].inputSchema["properties"]
check("direct tool exposes client_login", "client_login" in add_props)
check("mutating tool exposes confirm (YD_CONFIRM on)", "confirm" in add_props)
metrika_props = tools["yd_metrika_report"].inputSchema["properties"]
check("metrika report has no client_login", "client_login" not in metrika_props)
aud_props = tools["yd_audience_segment_delete"].inputSchema["properties"]
check("audience tool has no client_login", "client_login" not in aud_props)
check("mutating audience tool exposes confirm (YD_CONFIRM on)", "confirm" in aud_props)
check("all audience tools dispatched", len(server._audience_dispatch) == len(server.AUDIENCE_TOOLS))

print("== Unified Performance Campaign payloads ==")
captured = []

async def capture_api501(_client, service, method, params):
    captured.append((service, method, params))
    return {"result": {"AddResults": [{"Id": 1}]}}

original_api501 = server._api501
server._api501 = capture_api501
try:
    asyncio.run(server._handle_unified_campaigns_add(None, {"campaigns": [{
        "name": "ЕПК тест", "start_date": "2026-09-03", "weekly_spend_limit": 1250,
        "counter_ids": [123], "exact_phrase_matching": True,
    }]}))
    service, method, params = captured.pop()
    campaign = params["Campaigns"][0]
    check("Unified campaign uses v501 handler", (service, method) == ("campaigns", "add"))
    check("Unified campaign is typed", "UnifiedCampaign" in campaign)
    check("Unified campaign budget is micros", campaign["UnifiedCampaign"]["BiddingStrategy"]["Search"]["WbMaximumClicks"]["WeeklySpendLimit"] == 1250000000)
    check("Unified campaign disables network", campaign["UnifiedCampaign"]["BiddingStrategy"]["Network"]["BiddingStrategyType"] == "SERVING_OFF")

    asyncio.run(server._handle_unified_adgroups_add(None, {"groups": [{
        "campaign_id": 11, "name": "Перевозчики", "region_ids": [0, -59],
    }]}))
    service, method, params = captured.pop()
    check("Unified ad group uses v501 handler", (service, method) == ("adgroups", "add"))
    check("Unified ad group has required offer-retargeting", params["AdGroups"][0]["UnifiedAdGroup"] == {"OfferRetargeting": "NO"})

    asyncio.run(server._handle_responsive_ads_add(None, {"ads": [{
        "ad_group_id": 22, "titles": ["ЭТрН под ключ"], "texts": ["Подключим и проведём первый рейс."], "href": "https://example.test",
    }]}))
    service, method, params = captured.pop()
    check("Responsive ad uses v501 handler", (service, method) == ("ads", "add"))
    check("Responsive ad is combinatorial", "ResponsiveAd" in params["Ads"][0])
finally:
    server._api501 = original_api501

print("== IAM expiresAt parsing ==")
from tools_direct_extra import iam_expiry  # noqa: E402
# 2026-05-30T13:00:00Z == epoch 1780146000
exp = iam_expiry({"expiresAt": "2026-05-30T13:00:00Z"}, now=0)
check("parses ISO Z to epoch (minus 60s safety)", abs(exp - (1780146000 - 60)) < 2)
exp_ns = iam_expiry({"expiresAt": "2026-05-30T13:00:00.123456789Z"}, now=0)
check("tolerates nanosecond precision", abs(exp_ns - (1780146000 - 60)) < 2)
fb = iam_expiry({}, now=1000)
check("falls back to now+11h when no expiresAt", fb == 1000 + 11 * 3600)
bad = iam_expiry({"expiresAt": "not-a-date"}, now=2000)
check("falls back on unparseable value", bad == 2000 + 11 * 3600)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("ALL PASSED")
