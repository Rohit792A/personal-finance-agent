# tests/test_model_config.py
"""Gemini 3 settings (DEC-042): model names per tier, retired names, request settings and replies.

No Google credentials needed: Vertex AI is replaced by fakes, and replies are built with the
Vertex AI SDK's own GenerationResponse so the reply reader is tested on real SDK objects.
"""
import json
import logging
import os
import subprocess
import sys
from types import SimpleNamespace

import pandas as pd
import pytest
from vertexai.generative_models import GenerationResponse

import config
from gemini import reply_text

APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
SIGNATURE = "c2lnbmF0dXJl"  # base64 of b"signature", like the thought signatures Gemini 3 attaches


def gemini_reply(*parts, finish_reason="STOP"):
    """A real SDK reply with the given parts, e.g. {"text": "...", "thought_signature": SIGNATURE}."""
    return GenerationResponse.from_dict({"candidates": [{
        "content": {"role": "model", "parts": list(parts)},
        "finish_reason": finish_reason,
    }]})


# ---------------------------------------------------------------------------------------------
# Model names and the retired-name fallback (config.py)
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-2.0-flash-001",
    "gemini-1.5-pro-002", "gemini-1.0-pro", "gemini-pro", "gemini-pro-vision",
    "GEMINI-2.5-FLASH", " gemini-2.5-flash ", "publishers/google/models/gemini-2.5-flash",
    "gemini-live-2.5-flash",
])
def test_retired_gemini_names_are_recognised(name):
    assert config.is_retired_model(name)


@pytest.mark.parametrize("name", [
    "gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.1-flash-lite",
    "gemini-3.1-pro-preview", "gemini-4-flash", "gemini-flash-latest", "",
])
def test_current_and_future_names_are_not_retired(name):
    assert not config.is_retired_model(name)


def test_tier_defaults_are_gemini_3():
    assert config.DEFAULT_MODEL_NAME == "gemini-3.5-flash"
    assert config.DEFAULT_MODEL_LITE == "gemini-3.5-flash-lite"
    assert config.DEFAULT_MODEL_PRO == "gemini-3.5-flash"
    for name in (config.MODEL_NAME, config.MODEL_LITE, config.MODEL_PRO):
        assert not config.is_retired_model(name)


def test_model_setting_uses_the_environment_when_set():
    env = {"MODEL_NAME": "gemini-3.5-flash"}
    assert config.model_setting(("MODEL_NAME", "GEMINI_MODEL"), "gemini-3.8-flash", env) == "gemini-3.5-flash"
    assert config.model_setting(("MODEL_NAME", "GEMINI_MODEL"), "gemini-3.8-flash", {}) == "gemini-3.8-flash"
    assert config.model_setting(("MODEL_NAME",), "gemini-3.8-flash", {"MODEL_NAME": "  "}) == "gemini-3.8-flash"


def test_model_name_wins_over_the_older_gemini_model_alias():
    names = ("MODEL_NAME", "GEMINI_MODEL")
    assert config.model_setting(names, "d", {"GEMINI_MODEL": "gemini-3.5-flash"}) == "gemini-3.5-flash"
    assert config.model_setting(names, "d", {"MODEL_NAME": "gemini-3.1-flash-lite",
                                             "GEMINI_MODEL": "gemini-3.5-flash"}) == "gemini-3.1-flash-lite"


def test_a_retired_name_falls_back_to_the_default_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="config"):
        chosen = config.model_setting(("MODEL_LITE",), "gemini-3.5-flash-lite", {"MODEL_LITE": "gemini-2.5-flash-lite"})
    assert chosen == "gemini-3.5-flash-lite"
    assert "MODEL_LITE=gemini-2.5-flash-lite" in caplog.text and "retired" in caplog.text


def _config_in_fresh_process(**env_overrides):
    """Imports config.py in a new interpreter with a clean model environment; returns (values, stderr)."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("LOCATION", "MODEL_NAME", "GEMINI_MODEL", "MODEL_LITE", "MODEL_PRO")}
    env.update(env_overrides)
    code = "import config, json; print(json.dumps([config.LOCATION, config.MODEL_NAME, config.MODEL_LITE, config.MODEL_PRO]))"
    done = subprocess.run([sys.executable, "-c", code], cwd=APP_DIR, env=env,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1]), done.stderr


def test_module_defaults_when_nothing_is_set():
    values, _ = _config_in_fresh_process()
    assert values == ["global", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.5-flash"]


def test_module_replaces_retired_names_from_the_environment():
    values, stderr = _config_in_fresh_process(MODEL_NAME="gemini-2.5-flash", MODEL_PRO="gemini-2.5-pro",
                                              MODEL_LITE="gemini-3.1-flash-lite")
    assert values == ["global", "gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-3.5-flash"]
    assert "MODEL_NAME=gemini-2.5-flash names a retired Gemini model" in stderr
    assert "MODEL_PRO=gemini-2.5-pro names a retired Gemini model" in stderr


# ---------------------------------------------------------------------------------------------
# Reading replies (gemini.py)
# ---------------------------------------------------------------------------------------------

def test_reply_with_a_thought_signature_reads_like_before():
    reply = gemini_reply({"text": '{"ok": true}', "thought_signature": SIGNATURE})
    assert reply_text(reply) == '{"ok": true}'


def test_reply_split_over_parts_is_joined_where_the_sdk_text_raises():
    reply = gemini_reply({"text": '{"transactions": [', "thought_signature": SIGNATURE}, {"text": "]}"})
    with pytest.raises(ValueError, match="Multiple content parts"):
        reply.text  # the SDK accessor the agents used before
    assert json.loads(reply_text(reply)) == {"transactions": []}


def test_thought_parts_and_signature_only_parts_are_not_part_of_the_answer():
    reply = gemini_reply({"text": "Thinking about the categories...", "thought": True},
                         {"text": "Travel"},
                         {"thought_signature": SIGNATURE})
    assert reply_text(reply) == "Travel"


def test_reply_without_answer_text_fails_without_echoing_the_reply():
    reply = gemini_reply({"text": "secret ledger row 12345", "thought": True}, finish_reason="MAX_TOKENS")
    with pytest.raises(ValueError) as error:
        reply_text(reply)
    assert "MAX_TOKENS" in str(error.value) and "12345" not in str(error.value)


def test_blocked_reply_without_candidates_fails_clearly():
    with pytest.raises(ValueError, match="no answer"):
        reply_text(GenerationResponse.from_dict({"candidates": []}))


def test_test_doubles_with_only_text_still_work():
    assert reply_text(SimpleNamespace(text="OK")) == "OK"


# ---------------------------------------------------------------------------------------------
# What the agents send: tier models, and no temperature or thinking budget
# ---------------------------------------------------------------------------------------------

class RecordingModel:
    """Stands in for GenerativeModel: records the model name and generation settings."""
    created = []

    def __init__(self, name, reply=None, **kwargs):
        self.name, self.kwargs, self.reply, self.configs = name, kwargs, reply, []
        RecordingModel.created.append(self)

    def generate_content(self, contents, generation_config=None):
        self.configs.append(generation_config)
        return self.reply

    def start_chat(self, history=None):
        model = self

        class Chat:
            def send_message(self, message, generation_config=None):
                model.configs.append(generation_config)
                return model.reply

        return Chat()


@pytest.fixture
def recording(monkeypatch):
    RecordingModel.created = []
    monkeypatch.setattr("vertexai.init", lambda **kwargs: None)
    return RecordingModel


def _install(monkeypatch, module, reply):
    monkeypatch.setattr(module.vertexai, "init", lambda **kwargs: None)
    monkeypatch.setattr(module, "GenerativeModel", lambda name, **kw: RecordingModel(name, reply, **kw))


def _row(day, amount, description, kind="EXPENSE"):
    return {"date": day, "amount": amount, "description": description, "category": "Food",
            "type": kind, "confidence": 95, "is_self_transfer": False}


def test_advisor_tiers_use_the_configured_models():
    from advisor import FinancialAdvisor, ModelTier

    advisor = FinancialAdvisor.__new__(FinancialAdvisor)  # the router needs no Vertex AI or database
    assert advisor._select_model_tier("Simulate retirement in 10 years", 2) is ModelTier.PRO
    assert advisor._select_model_tier("What is my balance", 1) is ModelTier.LITE
    assert advisor._select_model_tier("How can I spend less on food?", 3) is ModelTier.STANDARD
    assert (ModelTier.LITE.model, ModelTier.STANDARD.model, ModelTier.PRO.model) == (
        config.MODEL_LITE, config.MODEL_NAME, config.MODEL_PRO)
    # Standard and Pro share a model by default; they must stay separate tiers, not Enum aliases.
    assert ModelTier.PRO is not ModelTier.STANDARD and ModelTier.PRO.name == "PRO"


def test_advisor_chat_sends_no_temperature_or_thinking_budget(recording, monkeypatch):
    import advisor as advisor_module

    reply = gemini_reply({"text": "**Plan:** invest ", "thought_signature": SIGNATURE}, {"text": "monthly."})
    _install(monkeypatch, advisor_module, reply)
    advisor = advisor_module.FinancialAdvisor()
    monkeypatch.setattr(advisor, "_get_financial_summary_text", lambda: "Synthetic summary: income 1000.")

    answer = advisor.chat("Simulate retirement savings for 10 years", history=[])

    chat_model = recording.created[-1]
    assert chat_model.name == config.MODEL_PRO
    assert chat_model.configs == [{"top_p": 0.95}]
    assert answer == "**Plan:** invest monthly."


def test_advisor_insights_read_multi_part_replies(recording, monkeypatch):
    import advisor as advisor_module

    _install(monkeypatch, advisor_module, gemini_reply({"text": "1. Save "}, {"text": "more."}))
    advisor = advisor_module.FinancialAdvisor()
    monkeypatch.setattr(advisor, "_get_financial_summary_text", lambda: "Synthetic summary: income 1000.")

    assert advisor.generate_insights() == "1. Save more."
    assert recording.created[0].name == config.MODEL_NAME


def test_statement_and_receipt_readers_use_the_standard_model_without_temperature(recording, monkeypatch, tmp_path):
    import extractor
    import statement_agent

    statement = {"transactions": [_row("2026-10-03", 120.5, "Synthetic cafe")]}
    _install(monkeypatch, statement_agent, gemini_reply({"text": json.dumps(statement), "thought_signature": SIGNATURE}))
    pdf = tmp_path / "Synthetic statement October 2026.pdf"
    pdf.write_bytes(b"%PDF-1.4 synthetic")
    data = statement_agent.StatementExtractor().extract(str(pdf))
    assert [t.amount for t in data.transactions] == [120.5]

    receipt = _row("2026-10-04", 80, "Synthetic bakery")
    _install(monkeypatch, extractor, gemini_reply({"text": json.dumps(receipt)}))
    image = tmp_path / "IMG20261004101010.jpg"
    image.write_bytes(b"\xff\xd8\xff synthetic")
    assert extractor.ReceiptExtractor().extract(str(image)).amount == 80

    assert [m.name for m in recording.created] == [config.MODEL_NAME, config.MODEL_NAME]
    statement_model, receipt_model = recording.created
    # Statements also set the reply budget (DEC-060): long statements are read a few pages per call.
    assert statement_model.configs == [{"response_mime_type": "application/json",
                                        "max_output_tokens": statement_agent.MAX_OUTPUT_TOKENS}]
    assert receipt_model.configs == [{"response_mime_type": "application/json"}]


def test_spreadsheet_importer_uses_the_standard_model_without_temperature(recording, monkeypatch, tmp_path):
    import ai_importer

    sheet_rows = {"transactions": [_row("2026-03-05", 40, "Synthetic canteen"),
                                   _row("2026-03-31", 1000, "Synthetic salary", "INCOME")]}
    _install(monkeypatch, ai_importer, gemini_reply({"text": json.dumps(sheet_rows), "thought_signature": SIGNATURE}))

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, statement, params=None):
            return SimpleNamespace(fetchone=lambda: None)

    class FakeDatabase:
        pool = SimpleNamespace(connect=FakeConnection, begin=FakeConnection)

        def save_transactions_batch(self, data_list, gcs_uri, **kwargs):
            return list(range(len(data_list)))

        def close(self):
            pass

    monkeypatch.setattr(ai_importer, "DatabaseManager", FakeDatabase)
    sheet = tmp_path / "Synthetic budget March 2026.xlsx"
    pd.DataFrame({"Date": ["3/5", "3/31"], "Amount": [40, 1000]}).to_excel(sheet, index=False)

    result = ai_importer.AISpreadsheetImporter().import_sheet_verbatim(str(sheet))

    assert result["total"] == 2
    assert recording.created[0].name == config.MODEL_NAME
    assert recording.created[0].configs == [{"response_mime_type": "application/json"}]


def test_voice_agent_uses_the_lite_model(recording, monkeypatch):
    import vertexai.generative_models
    from voice_agent import VoiceAgent

    reply = gemini_reply({"text": "Thinking...", "thought": True}, {"text": "Personal", "thought_signature": SIGNATURE})
    monkeypatch.setattr(vertexai.generative_models, "GenerativeModel", lambda name, **kw: RecordingModel(name, reply))
    monkeypatch.setattr(VoiceAgent, "_model_initialized", False)
    monkeypatch.setattr(VoiceAgent, "_shared_model", None)

    # No keyword rule matches this reply, so it goes to Gemini.
    assert VoiceAgent().resolve_category("that was for my nephew's thing") == "Personal"
    assert recording.created[0].name == config.MODEL_LITE
    assert recording.created[0].configs == [None]
