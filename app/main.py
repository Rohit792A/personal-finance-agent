# main.py
"""Core Multimodal Document Ingestion Orchestrator (`process_file`).

FDE Pillar 1 (BUILD) & Pillar 2 (GOVERN):
- Routes incoming financial documents by extension across three specialized AI agents:
  1. Route A (`.pdf`): `StatementExtractor` for multi-page bank statements.
  2. Route B (`.jpg`, `.jpeg`, `.png`): `ReceiptExtractor` for merchant receipt images.
  3. Route C (`.xlsx`, `.xls`): `AISpreadsheetImporter` for historical Excel ledgers.
- Applies deterministic Human-in-the-Loop (HITL) confidence gating: any row with
  `confidence < 85.0` or an unrecognized category is flagged with `requires_human_review = True`
  so the user can resolve it via the Voice Clarification loop (`VoiceAgent`).
- Duplicates (DEC-034): a PDF that was already imported (same SHA-256) is skipped before any AI
  call, and rows already in the ledger are reconciled by `DatabaseManager`; the messages say how
  many were recognised and where they came from.
- Long statements (DEC-060): `StatementExtractor` reads a PDF a few pages per call; pages where it
  read fewer rows than the page text shows are reported in the messages, and the PDF route returns
  the batch counts (`summary`: rows, added, already, ...) for the Bank Sync message.
"""
import logging
import os
import re
import sqlalchemy

import config
from extractor import ReceiptExtractor
from statement_agent import LOCKED_PDF_MESSAGE, StatementExtractor, decrypt_pdf, pdf_needs_password
from ai_importer import AISpreadsheetImporter, compute_file_hash
from database import DatabaseManager, FileAlreadyImported
from exceptions import AgentError, PasswordRequiredError
from sqlalchemy.exc import SQLAlchemyError
from voice_agent import VoiceAgent
from security.model_armor import ModelArmor
from security.dlp import FinancialDLPPipeline
from telemetry.tracer import AgentTracer


logger = logging.getLogger(__name__)


def _already_imported_message(file_name, imported_at=None, rows_imported=None) -> str:
    name = os.path.basename(file_name or "") or "This file"
    when = f" on {imported_at:%d %b %Y}" if imported_at else " before"
    rows = f" ({rows_imported} transactions)" if rows_imported else ""
    return f"'{name}' was already imported{when}{rows}, so nothing was added again."


def _duplicate_notes(summary, receipt: bool = False) -> list[str]:
    """Upload messages about rows that were already in the ledger (DatabaseManager.last_batch_summary)."""
    if not summary:
        return []
    notes = []
    already, rows = summary.get("already", 0), summary.get("rows", 0)
    if already:
        names = []
        for label, _count in summary.get("sources", [])[:2]:
            if label == "Gmail alerts":
                names.append("your Gmail alerts")
            elif label == "entered by hand":
                names.append("rows entered by hand")
            else:
                names.append(f"'{label}'")
        where = f" (from {' and '.join(names)})" if names else ""
        if receipt:
            notes.append(f"This receipt matches a transaction already in your ledger{where}, so it wasn't added again.")
        else:
            verb, pronoun = ("was", "it wasn't") if already == 1 else ("were", "they weren't")
            notes.append(f"{already} of {rows} transactions {verb} already in your ledger{where}, so {pronoun} added again.")
    conflicts = summary.get("conflicts", 0)
    if conflicts:
        noun, pronoun = ("transaction", "it") if conflicts == 1 else ("transactions", "them")
        notes.append(f"{conflicts} of those {noun} came with a different type (expense / income / transfer) "
                     f"this time; the ledger kept its label and marked {pronoun} for review.")
    repeated = summary.get("repeated_in_file", 0)
    if repeated:
        verb = "was" if repeated == 1 else "were"
        notes.append(f"{repeated} line(s) exactly repeated another line in this file (same date, amount and text) and {verb} saved once.")
    return notes


def _batch_counts(summary) -> dict:
    """The counts of DatabaseManager.last_batch_summary that callers report (rows read, added, ...)."""
    summary = summary or {}
    return {key: int(summary.get(key, 0) or 0) for key in ("rows", "added", "already", "conflicts", "repeated_in_file")}


def _saved_statement_passwords() -> list[str]:
    """The statement PDF passwords saved for Bank Sync (Secret Manager; personal backend only)."""
    if config.IS_DEMO:
        return []
    from gmail_sync import _load_statement_passwords  # lazy: Gmail sync isn't needed for most uploads

    return [p.strip() for p in re.split(r"[,;\n]+", _load_statement_passwords() or "") if p.strip()]


def _unlock_for_reading(path: str, pdf_password: str = "") -> bool:
    """Makes a password-protected PDF readable before it reaches Gemini, which rejects such files
    with 400 INVALID_ARGUMENT (DEC-060). Tries the password sent with the upload, then the saved
    statement passwords, and replaces the file with its unlocked copy. True if it was locked.
    Passwords are only used in memory: never logged or stored.
    """
    with open(path, "rb") as f:
        data = f.read()
    if not pdf_needs_password(data):
        return False
    candidates = ([pdf_password.strip()] if (pdf_password or "").strip() else []) + _saved_statement_passwords()
    unlocked = decrypt_pdf(data, candidates)
    if unlocked is None:
        if (pdf_password or "").strip():
            raise PasswordRequiredError("That password doesn't open this PDF. Check it and upload the file again.")
        raise PasswordRequiredError(LOCKED_PDF_MESSAGE)
    with open(path, "wb") as f:
        f.write(unlocked)
    logger.info("Unlocked password-protected PDF %s before reading it.", os.path.basename(path))
    return True


def process_file(file_path_or_uri: str, pdf_password: str = ""):
    """Dispatches a local file or GCS URI to the appropriate extractor agent and persists results.

    `pdf_password` (uploads only) opens a password-protected PDF; it is never stored.
    The whole import is one trace (root span `ingestion.process_file`, DEC-063).
    """
    tracer = AgentTracer(workflow_name="process_file_ingestion")
    file_type = os.path.splitext(str(file_path_or_uri).lower())[1]
    with tracer.request_span("ingestion.process_file", {"file_type": file_type}):
        return _process_file(file_path_or_uri, pdf_password, tracer)


def _guardrail_attributes(armor_results, dlp_results) -> dict:
    """Span attributes: how many rows each Model Armor / redaction layer decided (DEC-063)."""
    attributes = {f"model_armor.rows.{layer}": count for layer, count in ModelArmor.summarize_layers(armor_results).items()}
    for result in dlp_results:
        key = f"dlp.rows.{getattr(result, 'decision_layer', 'regex')}"
        attributes[key] = attributes.get(key, 0) + 1
    return attributes


def _process_file(file_path_or_uri: str, pdf_password: str, tracer: AgentTracer):
    """The body of `process_file`."""
    logger.info(f"--- Triggering Workflow for {file_path_or_uri} ---")
    db_manager = None

    user_messages = []

    try:
        file_extension = os.path.splitext(file_path_or_uri.lower())[1]

        #---------------------------------
        # ROUTE A: PDF Bank Statements
        #---------------------------------

        if file_extension == ".pdf":
            logger.info("PDF detected. Routing to Statement Extractor...")
            db_manager = DatabaseManager()
            # The same statement imported twice (a re-upload, or two Gmail syncs at once) is
            # skipped here, before any AI call; the claim inside the save closes the race.
            file_hash = compute_file_hash(file_path_or_uri) if os.path.isfile(file_path_or_uri) else None
            previous = db_manager.find_ingested_file(file_hash) if file_hash else None
            if previous:
                logger.warning(f"Identical file already imported ({previous[0]} at {previous[1]}); skipped.")
                return {"status": "success", "messages": [_already_imported_message(*previous)]}

            # The hash above is of the file as uploaded, so re-uploading the same locked file is
            # still recognised; the unlocked copy is what Gemini reads.
            if os.path.isfile(file_path_or_uri) and _unlock_for_reading(file_path_or_uri, pdf_password):
                user_messages.append("Unlocked the password-protected PDF.")

            with tracer.start_span("ingestion_agent.extract_pdf", {"file": os.path.basename(file_path_or_uri)}):
                agent = StatementExtractor()
                statement_data = agent.extract(file_path_or_uri)

            # "Found N transactions" is also read back by gmail_sync for the statement's count.
            page_count = getattr(agent, "pages_read", 0)
            pages = f" ({page_count} page{'' if page_count == 1 else 's'})" if page_count else ""
            user_messages.append(f"Found {len(statement_data.transactions)} transactions in the PDF{pages}.")
            # Pages where fewer rows were read than the page text shows (DEC-060).
            user_messages.extend(f"⚠️ {note}" for note in getattr(agent, "coverage_notes", []))

            # Fetch valid categories from the database
            with db_manager.pool.connect() as conn:
                valid_cats = {row[0] for row in conn.execute(sqlalchemy.text("select lower(name) FROM categories")).fetchall()}

            flagged_count = 0
            learned_applied = 0
            with tracer.start_span("clarification_and_reconciliation.screen_rows", {"rows": len(statement_data.transactions)}) as screen_span:
                # Model Armor + DLP results for every narration, computed up front (DEC-063): with the
                # Google APIs on, a few batched requests cover all rows; without them each row gets
                # the same regex screen as before.
                raw_descs = [getattr(tx, "description", "") or "" for tx in statement_data.transactions]
                armor_results = ModelArmor.inspect_prompts(raw_descs)
                safe_rows = [i for i, res in enumerate(armor_results) if res.is_safe]
                dlp_results = dict(zip(safe_rows, FinancialDLPPipeline.redact_texts([raw_descs[i] for i in safe_rows])))
                tracer.add_attributes(screen_span, _guardrail_attributes(armor_results, dlp_results.values()))
                for row, tx in enumerate(statement_data.transactions):
                    # 1. Ingestion Agent Guardrails: Model Armor + DLP screening on narration
                    armor_res = armor_results[row]
                    if not armor_res.is_safe:
                        tx.description = "[REDACTED_UNSAFE_NARRATION]"
                    else:
                        dlp_res = dlp_results[row]
                        if dlp_res.redacted_count > 0:
                            tx.description = dlp_res.sanitized_text

                    # 2. Reconciliation Agent: Self-transfer detection
                    if getattr(tx, 'is_self_transfer', False):
                        tx.category = "Transfer"
                        tx.transaction_type = "TRANSFER"
                        tx.requires_human_review = False
                        user_messages.append(f"Self-transfer detected: {tx.description} (₹{tx.amount})")
                        continue

                    # 3. Clarification Agent: Check learned user rules before flagging for human review
                    learned = db_manager.match_learned_rule(tx.description)
                    if learned:
                        tx.category = learned["category"]
                        tx.transaction_type = learned.get("transaction_type", getattr(tx, "transaction_type", "EXPENSE"))
                        tx.confidence = 99.0
                        tx.requires_human_review = False
                        learned_applied += 1
                        continue

                    safe_cat = (tx.category or "").strip().lower()

                    if tx.confidence < 85.0 or safe_cat not in valid_cats:
                        logger.warning(f"Flagged for human review: {tx.description} ({tx.category}, confidence {tx.confidence})")
                        tx.requires_human_review = True
                        flagged_count += 1
                    else:
                        tx.requires_human_review = False

            if learned_applied > 0:
                user_messages.append(f"Auto-categorized {learned_applied} transaction(s) using your learned rules.")
            if flagged_count > 0:
                user_messages.append(f"Flagged {flagged_count} transactions requiring human review.")

            try:
                with tracer.start_span("reconciliation_agent.save_batch", {"rows": len(statement_data.transactions)}):
                    db_manager.save_transactions_batch(
                        statement_data.transactions, file_path_or_uri,
                        file_hash=file_hash, file_name=os.path.basename(file_path_or_uri),
                    )
            except FileAlreadyImported as already:
                # Another import of the same file (e.g. a second sync) committed first.
                logger.warning(f"Identical file imported concurrently ({already.file_name}); nothing added.")
                return {"status": "success", "messages": [
                    _already_imported_message(already.file_name, already.imported_at, already.rows_imported)
                ]}
            user_messages.extend(_duplicate_notes(db_manager.last_batch_summary))
            tracer.export_summary()
            logger.info(f"Saved {len(statement_data.transactions)} PDF transactions in batch.")
            return {"status": "success", "messages": user_messages,
                    "summary": _batch_counts(db_manager.last_batch_summary)}

        
        #---------------------------------
        # ROUTE B: Image Receipts
        #---------------------------------

        elif file_extension in [".jpg", ".jpeg", ".png"]:
            logger.info("Image detected. Routing to Receipt Extractor...")
            db_manager = DatabaseManager()
            with tracer.start_span("ingestion_agent.extract_receipt", {"file": os.path.basename(file_path_or_uri)}):
                agent = ReceiptExtractor()
                receipt_data = agent.extract(file_path_or_uri)

            # Screen narration with Model Armor + DLP
            raw_desc = getattr(receipt_data, "description", "") or ""
            if not ModelArmor.inspect_prompt(raw_desc).is_safe:
                receipt_data.description = "[REDACTED_UNSAFE_NARRATION]"
            else:
                dlp_res = FinancialDLPPipeline.redact_text(raw_desc)
                if dlp_res.redacted_count > 0:
                    receipt_data.description = dlp_res.sanitized_text

            # Fetch valid categories from the database
            with db_manager.pool.connect() as conn:
                valid_cats = {row[0] for row in conn.execute(sqlalchemy.text("select lower(name) FROM categories")).fetchall()}

            learned = db_manager.match_learned_rule(receipt_data.description)
            if learned:
                receipt_data.category = learned["category"]
                receipt_data.confidence = 99.0
                receipt_data.requires_human_review = False
                user_messages.append(f"Categorized as {receipt_data.category} using your learned rule.")
            else:
                safe_cat = (receipt_data.category or "").strip().lower()

                if receipt_data.confidence < 85.0 or safe_cat not in valid_cats:
                    logger.warning(f"Flagging receipt for human review: {receipt_data.description} (Category: {receipt_data.category})")
                    receipt_data.requires_human_review = True
                    user_messages.append(f"Receipt flagged for review: AI unsure about '{receipt_data.category}'.")
                else:
                    receipt_data.requires_human_review = False
                    user_messages.append(f"AI confidently categorized this as {receipt_data.category}.")

            tx_id = db_manager.save_transaction(receipt_data, file_path_or_uri)
            user_messages.extend(_duplicate_notes(db_manager.last_batch_summary, receipt=True))
            tracer.export_summary()
            logger.info(f"Saved Image transaction ID: {tx_id}")
            return {"status":"success","messages":user_messages}

        # ROUTE C: Excel Historical Data
        elif file_extension in [".xlsx", ".xls"]:
            logger.info("Excel spreadsheet detected. Routing to AI Spreadsheet Importer...")
            ai_importer = AISpreadsheetImporter()
            result = ai_importer.import_sheet_verbatim(file_path_or_uri)
            if isinstance(result, dict) and result.get("status") == "skipped":
                user_messages.append(result.get("message", "File was already imported."))
            else:
                total_cnt = result.get("total", 0) if isinstance(result, dict) else 0
                user_messages.append(f"Successfully processed Excel spreadsheet ({total_cnt} entries recorded).")
                user_messages.extend(_duplicate_notes(getattr(ai_importer.db_manager, "last_batch_summary", None)))
            return {"status": "success", "messages": user_messages}
            
        # ROUTE D: Catch-All
        else:
            logger.warning(f"Unsupported file type: '{file_extension}'. Aborting safely.")
            raise ValueError(f"Unsupported file extension: {file_extension}")

    except AgentError as e:
        logger.error(f"Agent workflow failed safely: {e}")
        raise

    except Exception as e:
        logger.critical(f"Unhandled system error: {e}", exc_info=True)
        raise

    finally:
        if db_manager:
            db_manager.close()
            logger.info("Database connection closed cleanly.")


if __name__ == "__main__":
    # Point this to your test file to test the orchestrator!
    test_file = "../data/IMG20260830060404.jpg"  # TODO: replace with your actual test file
    process_file(test_file)