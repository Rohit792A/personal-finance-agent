# extractor.py
"""Multimodal Receipt Image Extraction Agent (Vertex AI Gemini, `config.MODEL_NAME`).

FDE Pillar 1 (BUILD):
- Accepts `.jpg`, `.jpeg`, and `.png` receipt photographs from local disk or GCS (`gs://`).
- Uses Gemini multimodal vision (Gemini 3.5 Flash by default, DEC-042) to extract date, total
  amount, merchant description, category, and AI confidence score (0-100) into a validated
  `ReceiptData` contract.
- Dates (DEC-035): the prompt carries today's date and the file name (phone photos such as
  IMG20260830... name the day they were taken), and `date_guard.fix_years` checks the year.
"""
import logging
import json
import vertexai
from vertexai.generative_models import GenerativeModel, Part
from pydantic import ValidationError

from config import PROJECT_ID, LOCATION, MODEL_NAME
from date_guard import fix_years, prompt_context
from gemini import generate_with_timeout, reply_text
from models import ReceiptData
from exceptions import ExtractionError
from telemetry.tracer import model_call_span

logger = logging.getLogger(__name__)


class ReceiptExtractor:
    """Extracts a single verified `ReceiptData` record from a receipt image."""
    def __init__(self):
        self.date_notes = []
        try:
            vertexai.init(project=PROJECT_ID, location=LOCATION)
            self.model = GenerativeModel(MODEL_NAME)
        except Exception as e:
            logger.error(f"Failed to initialize Vertex AI: {e}")
            raise ExtractionError("Vertex AI initialization failed") from e

    def extract(self, gcs_uri: str) -> ReceiptData:
        """Runs multimodal vision OCR + structured JSON extraction on a receipt image."""
        logger.info(f"Starting extraction for URI: {gcs_uri}")
        self.date_notes = []

        try:
            # image_part = Part.from_uri(uri=gcs_uri, mime_type="image/jpeg")

            # Smart routing for Local vS Cloud file
            if gcs_uri.startswith("gs://"):
                # it is a cloud storage file
                image_part= Part.from_uri(uri=gcs_uri, mime_type="image/jpeg")
            else:
                # it is a local file    
                #with open(gcs_uri,"rb") as f:
                 #   pdf_bytes = f.read()
                # It is a local file
                ext = gcs_uri.lower().split(".")[-1]
                mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}
                mime_type = mime_map.get(ext, "image/jpeg")
                with open(gcs_uri, "rb") as f:
                    image_bytes = f.read()
                image_part = Part.from_data(data=image_bytes, mime_type=mime_type)
            from database import DatabaseManager

            prompt = """
            Extract transaction details from this receipt.
            Return ONLY a raw JSON object with keys: "date" (YYYY-MM-DD), "amount" (number), "description" (string), "category" (string), and "confidence" (number 0-100) and "is_self_transfer" (boolean).
            """ + prompt_context(gcs_uri) + DatabaseManager.format_learned_rules_prompt()

            # One span per Gemini call, with its token usage (DEC-063).
            with model_call_span("vertex_ai.receipt_reader", MODEL_NAME, "receipt_reader") as call:
                response = generate_with_timeout(
                    self.model,
                    [image_part, prompt],
                    generation_config={"response_mime_type": "application/json"},
                )
                call.record(response)

            raw_data = json.loads(reply_text(response))

            # This is where the magic happens: Pydantic validates the AI's output
            validated_data = ReceiptData(**raw_data)
            # A year the model filled in on its own is put back in the file's period (DEC-035).
            self.date_notes = fix_years([validated_data], gcs_uri)
            logger.info("Successfully extracted and validated receipt data.")
            return validated_data

        except json.JSONDecodeError as e:
            logger.error(f"Gemini returned invalid JSON: {e}")
            raise ExtractionError("Invalid JSON from LLM") from e

        except ValidationError as e:
            logger.error(f"Data validation failed: {e}")
            raise ExtractionError("Extracted data did not match required schema") from e

        except Exception as e:
            logger.error(f"Unexpected error during extraction: {e}")
            raise ExtractionError("Extraction process failed") from e