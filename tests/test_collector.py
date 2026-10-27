"""Offline tests: only invented local data; no SEC access or downloaded fixtures."""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from investor_ai.cli import main
from investor_ai.collector import Collector, TICKERS_URL
from investor_ai.financials import extract_facts, filing_index, period_type
from investor_ai.sec import NoRedirect, Response, SecClient, SecError, decode_json, normalize_cik
from investor_ai.storage import Store

FIXTURES = Path(__file__).parent / "fixtures"
CIK = "0000123456"
URL = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json"
UA = "InvestorAI tests contact@example.org"


def fixtures():
    return decode_json((FIXTURES / "companyfacts.json").read_bytes()), json.loads((FIXTURES / "submissions.json").read_text())


class FakeClient:
    def __init__(self):
        self.facts, self.submissions = fixtures()
        self.calls = []
        self.fail_facts = False

    def get(self, url):
        self.calls.append(url)
        if url == TICKERS_URL:
            body = {"0": {"ticker": "SYNTH", "cik_str": 123456, "title": "Synthetic"}}
        elif "companyfacts" in url:
            if self.fail_facts:
                raise SecError("Synthetic failure")
            body = self.facts
        else:
            body = self.submissions
        return Response(url, json.dumps(body).encode(), datetime.now(timezone.utc).isoformat(), {})


class FinancialTests(unittest.TestCase):
    def setUp(self):
        self.facts, self.submissions = fixtures()
        self.rows = extract_facts(self.facts, self.submissions, retrieved_at="2025-01-01T00:00:00+00:00", source_url=URL)

    def test_quarter_is_fiscal_not_calendar_and_not_container_year(self):
        current = next(r for r in self.rows if r["metric"] == "revenue" and r["value"] == "300")
        prior = next(r for r in self.rows if r["metric"] == "revenue" and r["value"] == "250")
        self.assertEqual((current["fiscal_year"], current["fiscal_quarter"]), (2024, 1))
        self.assertEqual(current["frame"], "CY2023Q4")
        self.assertIsNone(prior["fiscal_year"])
        self.assertIsNone(prior["fiscal_quarter"])
        self.assertEqual(prior["filing_fiscal_year"], 2024)
        self.assertEqual(prior["period_type"], "quarterly")

    def test_revisions_concepts_and_currencies_are_not_coalesced(self):
        rows = [r for r in self.rows if r["metric"] == "revenue" and r["start"] == "2022-10-01"]
        self.assertEqual({r["value"] for r in rows}, {"1000", "1100", "1200", "900"})
        revised = next(r for r in rows if r["value"] == "1100")
        self.assertEqual(revised["filing_fiscal_year"], 2024)
        self.assertEqual(revised["fiscal_year"], 2023)
        self.assertEqual(revised["fiscal_label_basis"], "matching_reported_period")
        old = [r for r in rows if r["filed"] <= "2023-12-31"]
        self.assertNotIn("1100", {r["value"] for r in old})
        self.assertEqual(len({r["record_id"] for r in rows}), 4)

    def test_cumulative_cash_flow_is_not_a_standalone_quarter(self):
        rows = [r for r in self.rows if r["metric"] == "operating_cash_flow"]
        quarterly = [r for r in rows if r["period_type"] == "quarterly"]
        self.assertEqual([r["value"] for r in quarterly], ["20"])
        cumulative = next(r for r in rows if r["value"] == "75")
        self.assertEqual(cumulative["period_type"], "year_to_date")
        self.assertIsNone(cumulative["fiscal_quarter"])
        self.assertEqual(cumulative["fiscal_period"], "Q3")
        self.assertTrue(cumulative["is_year_to_date"])
        self.assertEqual(cumulative["classification"], "reported")

    def test_zero_negative_units_and_provenance(self):
        cash = next(r for r in self.rows if r["metric"] == "cash")
        self.assertEqual((cash["value"], cash["period_type"]), ("0", "instant"))
        self.assertEqual(cash["acceptance_datetime"], "2023-11-01T20:00:00Z")
        self.assertIsNone(cash["public_available_at"])
        self.assertEqual(cash["source_observation"]["val"], 0)
        self.assertIn(cash["accession"], cash["filing_url"])
        self.assertIn("/" + cash["accession"].replace("-", "") + "/", cash["filing_url"])
        self.assertEqual(cash["source_url"], URL)
        self.assertEqual(next(r for r in self.rows if r["metric"] == "net_income")["value"], "-50")
        self.assertIn("non_usd_unit", next(r for r in self.rows if r["unit"] == "EUR")["quality_flags"])

    def test_duration_classification_handles_52_53_week_years_and_stubs(self):
        self.assertEqual(period_type("2022-09-25", "2023-09-30"), ("annual", 371))
        self.assertEqual(period_type("2023-10-01", "2023-12-30"), ("quarterly", 91))
        self.assertEqual(period_type("2024-01-01", "2024-02-01")[0], "other")
        with self.assertRaises(ValueError):
            period_type("2024-02-01", "2024-01-01")

    def test_missing_older_filing_metadata_is_visible(self):
        self.submissions["filings"]["recent"] = {}
        rows = extract_facts(self.facts, self.submissions, retrieved_at="2025-01-01T00:00:00Z", source_url=URL)
        self.assertTrue(all("filing_metadata_not_in_loaded_submissions" in r["quality_flags"] for r in rows))
        self.assertTrue(all(r["fiscal_year"] is None for r in rows))

    def test_malformed_submissions_and_fact_identity_are_rejected(self):
        broken = deepcopy(self.submissions)
        broken["filings"]["recent"]["form"].pop()
        with self.assertRaises(SecError):
            filing_index(broken)
        broken = deepcopy(self.submissions)
        broken["cik"] = "2"
        with self.assertRaises(SecError):
            extract_facts(self.facts, broken, retrieved_at="now", source_url=URL)

    def test_decimal_json_is_preserved_exactly(self):
        parsed = decode_json(b'{"value": 123.1234567890123456789}')
        self.assertEqual(str(parsed["value"]), "123.1234567890123456789")
        with self.assertRaises(SecError):
            decode_json(b'{"value": NaN}')

    def test_malformed_fact_arrays_fail_with_a_data_error(self):
        self.facts["facts"]["us-gaap"]["Assets"]["units"]["USD"] = [None]
        with self.assertRaises(SecError):
            extract_facts(self.facts, self.submissions, retrieved_at="now", source_url=URL)

    def test_only_identical_observations_are_deduplicated(self):
        observations = self.facts["facts"]["us-gaap"]["Assets"]["units"]["USD"]
        observations.append(deepcopy(observations[0]))
        repeated = extract_facts(self.facts, self.submissions, retrieved_at="later", source_url=URL)
        self.assertEqual(len(repeated), len(self.rows))
        self.assertEqual({r["record_id"] for r in repeated}, {r["record_id"] for r in self.rows})


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.client = FakeClient()
        self.collector = Collector(self.client, self.store)

    def test_ticker_cik_resolution_cache_and_raw_integrity(self):
        result = self.collector.collect(["SYNTH", "CIK123456"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "success")
        before = len(self.client.calls)
        old = self.store.latest(CIK)
        result = self.collector.collect(["123456"])
        self.assertEqual(result[0]["status"], "cached")
        self.assertEqual(len(self.client.calls), before)
        self.assertEqual(old["retrieved_at"], self.store.latest(CIK)["retrieved_at"])
        raw = self.store.raw(old["sources"]["companyfacts"])
        self.assertEqual(json.loads(raw), self.client.facts)
        self.assertEqual(self.store.resolve_local("SYNTH"), CIK)

    def test_changed_source_creates_new_snapshot_without_rewriting_old(self):
        self.collector.collect(["SYNTH"])
        old = self.store.latest(CIK)
        original = self.store.records(old)
        self.client.facts["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]["val"] = 501
        self.collector.collect(["SYNTH"], refresh=True)
        new = self.store.latest(CIK)
        self.assertNotEqual(new["snapshot_id"], old["snapshot_id"])
        self.assertEqual(self.store.records(old), original)
        self.assertNotEqual(old["sources"]["companyfacts"]["sha256"], new["sources"]["companyfacts"]["sha256"])

    def test_failed_refresh_keeps_last_success_and_reports_failure(self):
        self.collector.collect(["SYNTH"])
        old = self.store.latest(CIK)
        self.client.fail_facts = True
        result = self.collector.collect(["SYNTH"], refresh=True)
        self.assertEqual(result[0]["status"], "failed")
        self.assertEqual(self.store.latest(CIK), old)
        status = json.loads((self.store.root / "companies" / CIK / "status.json").read_text())
        self.assertEqual(status["last_attempt_status"], "failed")
        self.assertEqual(status["last_successful_retrieval_at"], old["retrieved_at"])
        self.collector.collect(["SYNTH"])
        after_cache = json.loads((self.store.root / "companies" / CIK / "status.json").read_text())
        self.assertEqual(after_cache["last_failure"], status["error"])
        self.assertEqual(after_cache["last_successful_retrieval_at"], old["retrieved_at"])

    def test_missing_metric_is_partial_not_zero(self):
        del self.client.facts["facts"]["us-gaap"]["Assets"]
        result = self.collector.collect(["SYNTH"])[0]
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["missing_metrics"], ["assets"])
        self.assertFalse(any(r["metric"] == "assets" for r in self.store.records(self.store.latest(CIK))))

    def test_saved_inputs_replay_without_network_and_old_snapshot_is_accessible(self):
        self.collector.collect(["SYNTH"])
        original = self.store.latest(CIK)
        facts = decode_json(self.store.raw(original["sources"]["companyfacts"]))
        submissions = decode_json(self.store.raw(original["sources"]["submissions"]))
        rows = extract_facts(facts, submissions, retrieved_at=original["retrieved_at"], source_url=URL)
        self.assertEqual(rows, self.store.records(original))
        self.collector.collect(["SYNTH"], refresh=True)
        output = StringIO()
        with redirect_stdout(output):
            code = main(["--data-dir", self.temp.name, "inspect", CIK, "--snapshot", original["snapshot_id"], "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["manifest"], original)

    def test_optional_historical_metadata_is_loaded_and_preserved(self):
        old = deepcopy(self.client.submissions["filings"]["recent"])
        self.client.submissions["filings"]["recent"] = {}
        name = f"CIK{CIK}-submissions-001.json"
        self.client.submissions["filings"]["files"] = [{"name": name}]
        base_get = self.client.get
        def get(url):
            if url.endswith(name):
                return Response(url, json.dumps(old).encode(), datetime.now(timezone.utc).isoformat(), {})
            return base_get(url)
        self.client.get = get
        self.collector.collect([CIK], filing_history=True)
        manifest = self.store.latest(CIK)
        self.assertTrue(manifest["filing_history_complete"])
        self.assertEqual(len(manifest["sources"]["submissions_history"]), 1)
        self.assertTrue(all(r["acceptance_datetime"] for r in self.store.records(manifest)))

    def test_collect_cli_uses_config_and_reports_results(self):
        config = Path(self.temp.name) / "watchlist.toml"
        config.write_text('watchlist = ["SYNTH"]\n[cik_overrides]\nSYNTH = "123456"\n')
        output = StringIO()
        with patch("investor_ai.cli.SecClient", return_value=self.client), redirect_stdout(output):
            code = main(["--config", str(config), "--data-dir", self.temp.name, "collect", "--user-agent", UA])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())[0]["status"], "success")

    def test_unknown_company_does_not_stop_other_watchlist_members(self):
        result = self.collector.collect(["UNKNOWN", "SYNTH"])
        self.assertEqual([r["status"] for r in result], ["failed", "success"])

    def test_configured_ticker_override_is_verified(self):
        collector = Collector(self.client, self.store, cik_overrides={"WRONG": CIK})
        result = collector.collect(["WRONG"])
        self.assertEqual(result[0]["status"], "failed")
        self.assertIsNone(self.store.latest(CIK))

    def test_raw_tampering_is_detected(self):
        self.collector.collect(["SYNTH"])
        reference = self.store.latest(CIK)["sources"]["companyfacts"]
        (self.store.root / reference["path"]).write_bytes(b'{}')
        with self.assertRaises(SecError):
            self.store.raw(reference)
        result = self.collector.collect([CIK])[0]
        self.assertEqual(result["status"], "failed")
        self.assertIn("checksum", result["error"])

    def test_overlapping_jobs_are_rejected(self):
        with self.store.collection_lock():
            with self.assertRaises(SecError):
                self.collector.collect(["SYNTH"])

    def test_offline_cli_filters_keep_all_eligible_revisions(self):
        self.collector.collect(["SYNTH"])
        output = StringIO()
        with redirect_stdout(output):
            code = main(["--data-dir", self.temp.name, "inspect", "SYNTH", "--metric", "revenue",
                         "--period", "annual", "--filed-through", "2023-12-31", "--limit", "0", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual({r["value"] for r in payload["observations"]}, {"1000", "1200", "900"})
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(main(["--data-dir", self.temp.name, "inspect", "NOPE"]), 1)


class FakeHTTP(BytesIO):
    status = 200
    headers = {"Content-Type": "application/json"}

    def geturl(self):
        return URL


class HTTPTests(unittest.TestCase):
    def test_user_agent_requires_application_and_email_not_only_a_contact_url(self):
        for identity in [
            "InvestorAI/0.1 personal noncommercial research https://github.com/antoniesuciu/InvestorAI",
            "contact@example.org",
        ]:
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                SecClient(identity)
        self.assertEqual(SecClient(UA).user_agent, UA)

    def test_identity_validation_and_cik_padding(self):
        self.assertEqual(normalize_cik("CIK320193"), "0000320193")
        for invalid in ["0", "../32", "12345678901"]:
            with self.assertRaises(ValueError):
                normalize_cik(invalid)
        for invalid in ["", "anonymous", "\ncontact@example.org"]:
            with self.assertRaises(ValueError):
                SecClient(invalid)
        with self.assertRaises(ValueError):
            SecClient(UA, requests_per_second=11)

    def test_rate_limiting_and_identifiable_headers(self):
        now, starts = [0.0], []
        def sleep(delay):
            now[0] += delay
        def opener(request, timeout):
            starts.append(now[0])
            self.assertEqual(request.get_header("User-agent"), UA)
            self.assertEqual(timeout, 30)
            return FakeHTTP(b'{}')
        client = SecClient(UA, opener=opener, clock=lambda: now[0], sleep=sleep)
        client.get(URL)
        client.get(URL)
        client.get(URL)
        self.assertEqual(starts, [0, 0.5, 1.0])

    def test_transient_http_retries_honor_retry_after(self):
        calls, waits = [], []
        def opener(request, timeout):
            calls.append(1)
            if len(calls) == 1:
                raise HTTPError(URL, 429, "rate", {"Retry-After": "3"}, None)
            return FakeHTTP(b'{"ok": true}')
        client = SecClient(UA, opener=opener, sleep=waits.append)
        self.assertTrue(client.get(URL).json()["ok"])
        self.assertIn(3, waits)
        self.assertEqual(len(calls), 2)

    def test_forbidden_html_and_long_cooldown_fail_without_bypass(self):
        for code, headers in [(403, {}), (429, {"Retry-After": "120"})]:
            calls = []
            def opener(request, timeout):
                calls.append(1)
                raise HTTPError(URL, code, "blocked", headers, None)
            with self.assertRaises(SecError):
                SecClient(UA, opener=opener).get(URL)
            self.assertEqual(len(calls), 1)
        with self.assertRaises(SecError):
            SecClient(UA, opener=lambda *a, **k: FakeHTTP(b'<html>Access denied</html>')).get(URL)

    def test_network_retries_are_bounded(self):
        calls = []
        def opener(request, timeout):
            calls.append(1)
            raise URLError("offline")
        with self.assertRaises(SecError):
            SecClient(UA, retries=2, opener=opener, sleep=lambda _: None).get(URL)
        self.assertEqual(len(calls), 3)
        with self.assertRaises(ValueError):
            SecClient(UA).get("https://unapproved.example/data.json")

    def test_redirects_are_rejected_before_an_unbudgeted_follow_up(self):
        with self.assertRaises(SecError):
            NoRedirect().redirect_request(None, None, 302, "moved", {}, "https://unapproved.example")


if __name__ == "__main__":
    unittest.main()
