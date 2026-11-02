# app/ai_importer.py
"""AI-Powered Spreadsheet Importer with 2-Tier Cryptographic Deduplication.

FDE Pillar 1 (BUILD) & Pillar 4 (OPTIMIZE):
- Tier 1 (File-Level SHA-256 Gate): Computes a SHA-256 digest of the raw `.xlsx`/`.xls` file
  and checks `ingested_files` BEFORE calling Vertex AI—saving 100% of LLM tokens on re-uploads.
- Tier 2 (Table-Aware Verbatim Parsing): Uses Gemini (`config.MODEL_NAME`, Gemini 3.5 Flash by
  default) to parse side-by-side Expenses and Income tables verbatim into `StatementData`.
  Gemini 2.5 ran at `temperature=0.0`; Gemini 3 runs at its default temperature, as Google
  advises (DEC-042). The verbatim rules are in the prompt, and the rows are validated.
- Dates (DEC-035): the prompt carries today's date, the file name and the period it states, and
  `date_guard.fix_years` checks the returned years before saving; notes come back in "notes".
"""
import hashlib
import json
import logging
import pandas as pd
import sqlalchemy
import vertexai
from vertexai.generative_models import GenerativeModel

from config import LOCATION, MODEL_NAME, PROJECT_ID
from database import DatabaseManager
from date_guard import fix_years, prompt_context
from gemini import reply_text
from models import StatementData
from voice_agent import VoiceAgent
from exceptions import ExtractionError

logger = logging.getLogger(__name__)


def compute_file_hash(file_path: str) -> str:
    """Generates a unique SHA-256 fingerprint for the entire file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_row_signature(
    trans_date, amount: float, description: str, trans_type: str
) -> str:
    """Generates a unique deterministic fingerprint for a single transaction row."""
    normalized_str = f"{trans_date}|{float(amount):.2f}|{description.strip().lower()}|{trans_type.strip().upper()}"
    return hashlib.sha256(normalized_str.encode("utf-8")).hexdigest()


class AISpreadsheetImporter:
    """Imports historical Excel sheets with SHA-256 idempotency and Gemini table parsing."""
    
    def __init__(self):
        try:
            vertexai.init(project=PROJECT_ID, location=LOCATION)
            self.model = GenerativeModel(MODEL_NAME)
            self.db_manager = DatabaseManager()
        except Exception as e:
            raise ExtractionError("Failed to initialize AI Importer") from e

    def import_sheet_verbatim(self, excel_file_path: str):
        """Executes Tier-1 file hash check followed by Gemini verbatim table extraction."""
        logger.info(f"Checking file: {excel_file_path}...")

        #-------------------------------------------------------------
        # TIER 1: File-Level Check (Save tokens if file is identical)
        #-------------------------------------------------------------
        
        file_hash = compute_file_hash(excel_file_path)
        logger.info(f"File SHA-256 fingerprint: {file_hash[:12]}...")

        with self.db_manager.pool.connect() as conn:
            check_file = sqlalchemy.text(
                "SELECT imported_at FROM ingested_files WHERE file_hash = :hash LIMIT 1"
            )
            existing_file = conn.execute(
                check_file, {"hash": file_hash}
            ).fetchone()

            if existing_file:
                logger.warning(
                    f" IDENTICAL FILE DETECTED! Already imported on "
                    f"{existing_file[0]}. Skipping to save tokens and prevent duplicates."
                )
                return {
                    "status": "skipped",
                    "message": f"Identical file already imported on {existing_file[0]}",
                    "total": 0,
                    "expenses": 0,
                    "income": 0,
                }

        # Read raw spreadsheet into CSV string
        try:
            xl = pd.ExcelFile(excel_file_path)
            sheet_to_use = "Transactions" if "Transactions" in xl.sheet_names else xl.sheet_names[0]
            raw_df = pd.read_excel(excel_file_path, sheet_name=sheet_to_use)
            sheet_text = raw_df.to_csv(index=False)
        except Exception as e:
            logger.error(f"Failed to read Excel file {excel_file_path}: {e}")
            raise

        #--------------------------------------------------------------------------------------------------------------------------------
        # A strict, verbatim prompt so that the model does not hallucinate and provide incorrect tampered data. Gemini 2.5 also ran at
        # temperature = 0; Gemini 3 is tuned for its default temperature and may loop or degrade below it, so none is set (DEC-042).
        #--------------------------------------------------------------------------------------------------------------------------------

        prompt = f"""
        You are a strict data extraction parser. Extract tabular transactions from the provided raw spreadsheet data.

        The sheet contains two side by side tables:
        1. EXPENSES table (on the left)
        2. INCOME table (on the right)

        CRITICAL RULES:
        1. TYPE : If a row is in the left EXPENSES section, set "type": "EXPENSE". If a row is in the right INCOME section, set "type": "INCOME". 
        Do not guess based on the category name-look strictly at which table it belongs to.
        2. VERBATIM: Do NOT clean, summarize, or alter descriptions. Copy exact text.
        3. AMOUNTS: Do NOT round or modify amounts. Keep exact numbers.
        4. DATES: Format strictly as YYYY-MM-DD.
        5. CATEGORY: Extract the exact category string from the row as it is written in the spreadsheet.
        Do NOT attempt to map or correct it to a predefined list of categories.(Ex if it says "Dining" extract "Dining") We will handle this later
        6. EXTRACT ALL ROWS: Extract every non-empty row from both EXPENSES and INCOME sections.

        RAW SPREADSHEET DATA:
        {sheet_text}

        Return ONLY a raw JSON object with a single key "transactions" containing the list of objects with keys:
         "date", "amount", "description", "category","type", "confidence", "is_self_transfer" (boolean).
        "confidence" is a number from 0 to 100 (NOT 0 to 1): how sure you are about the row's category and type.
        {prompt_context(excel_file_path)}
        {getattr(DatabaseManager, "format_learned_rules_prompt", lambda: "")()}
        """

        logger.info(f" Sending sheet to Gemini ({MODEL_NAME}) for table-aware parsing...")
        response = self.model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )

        raw_json = json.loads(reply_text(response))
        validated_data = StatementData(**raw_json)

        # A year the model filled in on its own is put back in the file's period (DEC-035).
        date_notes = fix_years(validated_data.transactions, excel_file_path)

        # Filter out ₹0.00 template placeholder rows (unless it is an explicit opening/closing balance row)
        validated_data.transactions = [
            tx
            for tx in validated_data.transactions
            if abs(float(getattr(tx, "amount", 0.0) or 0.0)) >= 0.01
            or "balance" in str(getattr(tx, "description", "") or "").lower()
        ]

        # Log breakdown before saving
        expenses_found = [
            tx for tx in validated_data.transactions if tx.transaction_type == "EXPENSE"
        ]
        income_found = [
            tx for tx in validated_data.transactions if tx.transaction_type == "INCOME"
        ]

        logger.info(
            f" Gemini extracted {len(validated_data.transactions)} total rows:"
            f" {len(expenses_found)} expenses and {len(income_found)} income rows."
        )


        # # TIER 2: Row-Level UPSERT (Insert new rows / Update edited rows)
        # income_cats = {"savings", "paycheck", "bonus", "interest", "other"}


        # Initialize the Voice Agent
        # Save to PostgreSQL with cross-source reconciliation (prevents duplicating daily Gmail alerts)
        self.db_manager.save_transactions_batch(validated_data.transactions, excel_file_path)

        with self.db_manager.pool.begin() as db_conn:
            # Update file tracking
            log_query = sqlalchemy.text("""
                INSERT INTO ingested_files (file_hash, file_name, rows_imported)
                VALUES (:hash, :name, :count)
                ON CONFLICT (file_hash) DO NOTHING;
            """)

            db_conn.execute(
                log_query,
                {"hash": file_hash, "name": excel_file_path, "count": len(validated_data.transactions)},
            )

            logger.info(
                f" Success! Database updated:{len(expenses_found)} Expenses"
                f" and {len(income_found)} Income rows."
            )

        self.db_manager.close()
        return {
            "status": "success",
            "message": f"Database updated: {len(expenses_found)} Expenses and {len(income_found)} Income rows.",
            "total": len(validated_data.transactions),
            "expenses": len(expenses_found),
            "income": len(income_found),
            "notes": date_notes,
        }


if __name__ == "__main__":
    importer = AISpreadsheetImporter()
    importer.import_sheet_verbatim("../data/Monthly budget May 2025.xlsx")