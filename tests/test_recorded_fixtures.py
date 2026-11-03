# tests/test_recorded_fixtures.py
"""The recorded demo's fixtures (app/frontend/recorded-demo/recorded/, written by scripts/record_demo.py).

The recorded demo is published as a public static site, so these checks guard what it contains:
- the manifest is complete and every file it names exists, parses and is used (no stray files);
- every month the ledger shows has its analytics and budgets responses, so no screen comes up empty;
- the AI answers are real Gemini replies, not the app's offline fallback text;
- nothing looks like a credential, an e-mail address other than the fake demo one, the owner's name,
  a phone, card, PAN or Aadhaar number;
- the files stay small.
No network, database or credentials are needed.
"""
import json
import re
from datetime import date, datetime
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "app" / "frontend" / "recorded-demo" / "recorded"
MANIFEST = FIXTURES / "manifest.json"
DEMO_EMAIL = "demo@example.com"

pytestmark = pytest.mark.skipif(not MANIFEST.is_file(), reason="no recorded demo fixtures in this checkout")

FALLBACK_MARKERS = (
    "resilient fallback mode",
    "Vertex AI is currently initializing or offline",
    "Trouble analyzing finances",
    "### Master CFO Summary · ",
    "No response returned from AI Advisor",
)

# (name, pattern): any match in any fixture file fails the test.
FORBIDDEN = [
    ("JSON Web Token", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.")),
    ("bearer token", re.compile(r"(?i)\bbearer\s+(?!\[redacted\])[A-Za-z0-9._~+/=-]{8,}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("Google OAuth token", re.compile(r"ya29\.[0-9A-Za-z_-]+")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("workload token signature", re.compile(r"\|\d{9,11}\|[0-9a-f]{64}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("recorder's fake token", re.compile(r"recorded-demo-token")),
    ("owner's name or handle", re.compile(r"(?i)\brohit\b|\bkushwaha\b|rkushwaha")),
    # The owner's employer next to "salary" (a2a_orchestrator.py's Jarvis prompt still says "Typical TCS
    # Monthly Salary"). TCS on its own can also be Tax Collected at Source, so it isn't flagged alone.
    ("owner's employer", re.compile(r"(?i)\bTCS\b(?:\W+\w+){0,3}?\W+salar|salar\w*(?:\W+\w+){0,3}?\W+TCS\b|tata consultancy")),
    # A 10-digit Indian mobile number on its own (the sample data's UPI QR handles are not phones).
    ("phone number", re.compile(r"(?<![\w.])(?:\+91[\s-]?)?[6-9]\d{9}(?![\w@])")),
    ("card number", re.compile(r"(?<!\d)\d{16}(?!\d)")),
    ("Aadhaar number", re.compile(r"(?<!\d)\d{4} \d{4} \d{4}(?!\d)")),
    # ABCDE1234F is the placeholder PAN the Agent Registry screen sends.
    ("PAN", re.compile(r"\b(?!ABCDE1234F\b)[A-Z]{5}\d{4}[A-Z]\b")),
]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest():
    return load("manifest.json")


def referenced_files(manifest) -> set:
    files = set(manifest["datasets"].values())
    for section in ("routes", "chat", "copilot", "agent_tasks", "mcp_tools"):
        files.update(entry["file"] for entry in manifest[section])
    return files


def test_manifest_says_when_and_from_what(manifest):
    assert manifest["format"] == 1
    datetime.fromisoformat(manifest["recorded_at"].replace("Z", "+00:00"))
    date.fromisoformat(manifest["recorded_on"])
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["source"]["repository_commit"])
    assert manifest["source"]["uncommitted_backend_changes"] is False
    assert manifest["identity"] == {"email": DEMO_EMAIL, "provider": "password"}
    assert manifest["data"]["transactions"] > 0


def test_every_file_exists_parses_and_is_used(manifest):
    referenced = referenced_files(manifest)
    on_disk = {path.name for path in FIXTURES.iterdir()} - {"manifest.json"}
    assert referenced == on_disk, f"missing: {referenced - on_disk}, not in the manifest: {on_disk - referenced}"
    for name in referenced:
        load(name)


def test_route_keys_are_unique_and_canonical(manifest):
    keys = [entry["key"] for entry in manifest["routes"]]
    assert len(keys) == len(set(keys))
    for key in keys:
        method, _, target = key.partition(" ")
        assert method in {"GET", "POST"} and target.startswith("/")
        _, _, query = target.partition("?")
        if query:
            names = [pair.split("=", 1)[0] for pair in query.split("&")]
            assert names == sorted(names), f"query parameters of {key} must be sorted"


def test_the_signed_in_user_is_the_fake_demo_user():
    me = load("me.json")
    assert me["email"] == DEMO_EMAIL and me["environment"] == "demo"
    assert me["features"]["gmail_sync"] is False


def test_every_month_has_its_analytics_and_budgets(manifest):
    keys = {entry["key"] for entry in manifest["routes"]}
    months = [m["month"] for m in load("transactions-months.json")["months"]]
    assert len(months) == manifest["data"]["months"]
    for month in months:
        assert f"GET /analytics/summary?month={month}" in keys
        assert f"GET /budgets?month={month}" in keys
    for key in ("GET /me", "GET /transactions/months", "GET /analytics/summary", "GET /budgets",
                "GET /recurring", "GET /forecast", "GET /a2a/registry", "GET /agents/learned-rules",
                "GET /mcp/tools", "GET /agents/audit-trail?limit=20", "GET /telemetry/traces?limit=10"):
        assert key in keys


def test_all_time_budgets_scale_the_monthly_caps(manifest):
    """All Time compares the whole ledger's spending with each monthly cap times the months that have
    expenses (DEC-073), and agrees with the per-month budget responses."""
    all_time = load("budgets-all.json")
    ledger = load(manifest["datasets"]["ledger"])["transactions"]
    months = all_time["months_count"]
    assert months == len({row["date"][:7] for row in ledger if row["type"] == "EXPENSE"}) > 1
    per_month = [load(f"budgets-{m['month']}.json") for m in load("transactions-months.json")["months"]]
    assert all(response["months_count"] == 1 for response in per_month)
    for row in all_time["categories"]:
        same = [next(c for c in response["categories"] if c["category"] == row["category"]) for response in per_month]
        assert all(c["budget"] == c["monthly_budget"] == row["monthly_budget"] for c in same)
        assert row["budget"] == pytest.approx(row["monthly_budget"] * months)
        assert row["spent"] == pytest.approx(sum(c["spent"] for c in same), abs=0.01)
    assert all_time["total_budget"] == pytest.approx(all_time["monthly_total_budget"] * months)
    assert all_time["total_spent"] == pytest.approx(sum(row["spent"] for row in all_time["categories"]), abs=0.01)


def test_the_ledger_holds_every_transaction(manifest):
    ledger = load(manifest["datasets"]["ledger"])
    months = load("transactions-months.json")["months"]
    assert ledger["total"] == len(ledger["transactions"]) == manifest["data"]["transactions"]
    assert sum(m["count"] for m in months) == ledger["total"]
    assert len({row["id"] for row in ledger["transactions"]}) == ledger["total"]
    assert sum(1 for row in ledger["transactions"] if row["requires_human_review"]) == manifest["data"]["review_count"]


def test_ai_answers_are_real_gemini_replies(manifest):
    assert manifest["ai"]["enabled"] is True, "the published demo must carry the recorded AI answers"
    assert 6 <= len(manifest["chat"]) <= 10
    questions = [entry["question"].strip().lower() for entry in manifest["chat"]]
    assert len(questions) == len(set(questions))
    replies = [load(entry["file"])["reply"] for entry in manifest["chat"]]
    replies.append(load("insights.json")["insights"])
    replies.extend(load(entry["file"])["reply"] for entry in manifest["copilot"])
    for reply in replies:
        assert reply.strip()
        assert not any(marker in reply for marker in FALLBACK_MARKERS), reply[:200]
    assert manifest["ai"]["token_usage"]["calls_reported"] > 0
    assert "POST /upload" in {entry["key"] for entry in manifest["routes"]}


def test_no_credentials_or_personal_data():
    problems = []
    for path in sorted(FIXTURES.iterdir()):
        text = path.read_text(encoding="utf-8")
        for name, pattern in FORBIDDEN:
            for match in pattern.finditer(text):
                problems.append(f"{path.name}: {name}: {match.group(0)!r}")
        for match in EMAIL.finditer(text):
            if match.group(0).lower() != DEMO_EMAIL:
                problems.append(f"{path.name}: e-mail address: {match.group(0)!r}")
    assert not problems, "\n".join(problems[:20])


def test_fixtures_stay_small():
    sizes = {path.name: path.stat().st_size for path in FIXTURES.iterdir()}
    assert sum(sizes.values()) < 3_000_000, f"{sum(sizes.values()):,} bytes in total"
    assert max(sizes.values()) < 1_000_000, max(sizes, key=sizes.get)
