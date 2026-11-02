# app/voice_agent.py
"""Conversational Human-in-the-Loop (HITL) Voice Clarification Agent.

FDE Pillar 1 (BUILD) & Pillar 4 (OPTIMIZE):
- Resolves ambiguous transactions (`requires_human_review = True`, confidence `< 85%`) via a
  bidirectional voice loop:
  1. Text-to-Speech (TTS): Synthesizes natural spoken clarification prompts using Google Cloud
     TTS (`en-US-Neural2-F`) with an in-memory LRU-style audio byte cache (`_audio_cache`).
  2. Speech-to-Text (STT): Transcribes mobile/desktop voice replies (`webm`, `mp4`, `wav`, `ogg`)
     using Google Cloud Speech-to-Text as primary, with Vertex AI Gemini multimodal audio
     understanding as an automatic fallback.
  3. Hybrid Category Resolution: Evaluates deterministic Indian financial regex rules
     (`CATEGORY_RULES`) first (0-ms latency, $0 token cost) before falling back to Gemini.
- Gemini model (DEC-042): the lite tier, `config.MODEL_LITE` (Gemini 3.5 Flash-Lite by
  default). Both Gemini tasks here are short and interactive (map a reply to one of the
  categories, transcribe a few spoken words), so the faster model fits; it costs what
  Gemini 2.5 Flash, used before, did.
"""
import logging
import re
from typing import Optional

from config import PROJECT_ID, LOCATION, MODEL_LITE
from gemini import reply_text

logger = logging.getLogger(__name__)

VALID_CATEGORIES = [
    "Food",
    "Health/Medical",
    "Home",
    "Transportation",
    "Personal",
    "SIP",
    "Utilities",
    "Travel",
    "Debt",
    "Investment",
    "Stocks",
    "Rent",
    "Savings",
    "Paycheck",
    "Bonus",
    "Interest",
    "Transfer",
    "Other",
]

# High-precision semantic and keyword rules for category fallback
CATEGORY_RULES = [
    (
        "Transfer",
        [
            r"\b(self[\s\-]?transfer|internal[\s\-]?transfer|own[\s\-]?account|account[\s\-]?transfer|bank[\s\-]?transfer|fund[\s\-]?transfer)\b",
            r"\b(transfer(red)?\s+(to|from)?\s+(my|self|own|savings|checking|current|bank|other\s+account))\b",
            r"\b(transfer(red)?\s+(to|from)?\s*.*account)\b",
            r"\b(between\s+accounts|moved\s+funds|moved\s+money)\b",
            r"^\s*transfer\s*$",
        ],
    ),
    (
        "Paycheck",
        [
            r"\b(salary|paycheck|wages?|payroll|stipend|earnings?|remuneration)\b",
            r"^\s*paycheck\s*$",
        ],
    ),
    (
        "Bonus",
        [
            r"\b(bonus|incentive|performance[\s\-]?bonus|annual[\s\-]?bonus|festive[\s\-]?bonus|diwali[\s\-]?bonus|commission|reward)\b"
        ],
    ),
    (
        "Interest",
        [
            r"\b(interest|dividend|yield|interest[\s\-]?received|fd[\s\-]?interest|savings[\s\-]?interest|coupon)\b"
        ],
    ),
    (
        "SIP",
        [
            r"\b(sip|systematic[\s\-]?investment|mutual[\s\-]?funds?|index[\s\-]?funds?|etf|equity[\s\-]?funds?|amc|uti\s+mf|hdfc\s+mf|sbi\s+mf|zerodha\s+coin|groww\s+sip)\b"
        ],
    ),
    (
        "Stocks",
        [
            r"\b(stocks?|shares?|trading|demat|zerodha|groww|upstox|angel[\s\-]?one|nse|bse|portfolio)\b"
        ],
    ),
    (
        "Investment",
        [
            r"\b(invest(ment|ing)?|gold|sovereign[\s\-]?gold|sgb|crypto(currency)?|bitcoin|ethereum|fixed[\s\-]?deposit|fd|recurring[\s\-]?deposit|rd|bonds?|treasury|securities)\b"
        ],
    ),
    (
        "Debt",
        [
            r"\b(loans?|emi|credit[\s\-]?card|card[\s\-]?payment|card[\s\-]?bill|mortgage|debt|repay(ment)?|borrow(ed)?|due)\b"
        ],
    ),
    (
        "Rent",
        [
            r"\b(rent|house[\s\-]?rent|flat[\s\-]?rent|room[\s\-]?rent|office[\s\-]?rent|landlord|tenant|pg|paying[\s\-]?guest|society[\s\-]?maintenance|maintenance[\s\-]?fee)\b"
        ],
    ),
    (
        "Utilities",
        [
            r"\b(utilities|electricity|power[\s\-]?bill|electric[\s\-]?bill|water[\s\-]?bill|water|gas[\s\-]?bill|cylinder|piped[\s\-]?gas|lpg)\b",
            r"\b(internet|wifi|broadband|airtel|jio|vodafone|vi|mobile[\s\-]?recharge|phone[\s\-]?bill|postpaid|prepaid|dth|tata[\s\-]?play|dish[\s\-]?tv|bescom|tata[\s\-]?power)\b",
        ],
    ),
    (
        "Health/Medical",
        [
            r"\b(health|medical|medicines?|doctor|hospital|clinic|dental|dentist|pharmacy|pharma|chemist|apollo|1mg|pharmeasy|medplus|diagnostic|pathology|blood[\s\-]?test|prescription|surgery|physio|spectacles|lenskart)\b"
        ],
    ),
    (
        "Transportation",
        [
            r"\b(transport(ation)?|uber|ola|cab|taxis?|auto|rickshaw|rapido|metro|bus|train|irctc|railway|fuel|petrol|diesel|cng|gas[\s\-]?station|parking|toll|fastag|transit|commute|bike[\s\-]?ride|ride)\b"
        ],
    ),
    (
        "Travel",
        [
            r"\b(travel|trips?|vacation|holidays?|flights?|airlines?|air[\s\-]?ticket|hotels?|resorts?|airbnb|booking\.com|makemytrip|goibibo|cleartrip|indigo|vistara|air[\s\-]?india|tour|sightseeing)\b"
        ],
    ),
    (
        "Food",
        [
            r"\b(food|grocer(y|ies)|supermarket|dining|dinner|lunch|breakfast|brunch|snacks?|restaurants?|cafes?|coffee|starbucks|swiggy|zomato|blinkit|zepto|bigbasket|instamart|dunzo|pizza|burger|subway|mcdonalds?|kfc|dominos?|meals?|bakery|fruits?|veggies?|vegetables?)\b"
        ],
    ),
    (
        "Home",
        [
            r"\b(home|furniture|furnishing|ikea|appliance|refrigerator|washing[\s\-]?machine|plumber|electrician|carpenter|cleaning|urban[\s\-]?company|hardware|paint|decor|kitchenware|repairs?)\b"
        ],
    ),
    (
        "Savings",
        [
            r"\b(saving|savings|emergency[\s\-]?fund|ppf|epf|provident[\s\-]?fund|nps)\b"
        ],
    ),
    (
        "Personal",
        [
            r"\b(personal|shopping|clothes|clothing|apparel|shoes|footwear|fashion|amazon|flipkart|myntra|ajio|zara|h&m|haircut|salon|spa|beauty|cosmetics?|makeup|gym|fitness|cult\.fit|movies?|cinema|pvr|inox|netflix|spotify|hotstar|youtube|gaming|books?|kindle|course|udemy|coursera|gift)\b"
        ],
    ),
]


class VoiceAgent:
    _shared_model = None
    _model_initialized = False
    _shared_tts_client = None
    _tts_initialized = False
    _shared_stt_client = None
    _stt_initialized = False
    _audio_cache: dict[str, bytes] = {}

    def __init__(self):
        pass

    @property
    def model(self):
        if not VoiceAgent._model_initialized:
            VoiceAgent._model_initialized = True
            try:
                import vertexai
                from vertexai.generative_models import GenerativeModel

                vertexai.init(project=PROJECT_ID, location=LOCATION)
                VoiceAgent._shared_model = GenerativeModel(MODEL_LITE)
            except Exception as e:
                logger.warning(f"Vertex AI initialization skipped or unavailable: {e}")
        return VoiceAgent._shared_model

    @property
    def tts_client(self):
        if not VoiceAgent._tts_initialized:
            VoiceAgent._tts_initialized = True
            try:
                from google.cloud import texttospeech
                from google.api_core.client_options import ClientOptions

                options = ClientOptions(quota_project_id=PROJECT_ID)
                VoiceAgent._shared_tts_client = texttospeech.TextToSpeechClient(client_options=options)
            except Exception as e:
                logger.warning(f"Google Cloud TTS Client initialization skipped or unavailable: {e}")
        return VoiceAgent._shared_tts_client

    @property
    def stt_client(self):
        if not VoiceAgent._stt_initialized:
            VoiceAgent._stt_initialized = True
            try:
                from google.cloud import speech

                VoiceAgent._shared_stt_client = speech.SpeechClient()
            except Exception as e:
                logger.warning(f"Google Cloud Speech Client initialization skipped or unavailable: {e}")
        return VoiceAgent._shared_stt_client

    def get_question_text(self, description: str, amount: float, old_category: str) -> str:
        """Generates the text of the clarification question."""
        cat_disp = old_category if old_category and old_category.lower() != "other" else "Uncategorized"
        return (
            f"I found a ₹{amount:,.2f} transaction for {description}. "
            f"The current category is {cat_disp}, which needs clarification. "
            f"What should I categorize this as?"
        )

    def generate_question_audio(self, description: str, amount: float, old_category: str) -> Optional[bytes]:
        """Uses Google Cloud TTS to synthesize an audio question with in-memory caching.
        Returns MP3 audio bytes, or None if Cloud TTS is unavailable.
        """
        text = self.get_question_text(description, amount, old_category)
        if text in VoiceAgent._audio_cache:
            return VoiceAgent._audio_cache[text]

        if not self.tts_client:
            logger.warning("TTS client not available; cannot generate Cloud audio.")
            return None

        try:
            from google.cloud import texttospeech

            synthesis_input = texttospeech.SynthesisInput(text=text)
            voice = texttospeech.VoiceSelectionParams(
                language_code="en-US",
                name="en-US-Neural2-F",
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=1.05,
            )

            response = self.tts_client.synthesize_speech(
                input=synthesis_input, voice=voice, audio_config=audio_config
            )
            if response.audio_content:
                if len(VoiceAgent._audio_cache) > 256:
                    VoiceAgent._audio_cache.clear()
                VoiceAgent._audio_cache[text] = response.audio_content
            return response.audio_content
        except Exception as e:
            logger.warning(f"Google Cloud TTS speech synthesis failed: {e}")
            return None

    def transcribe_audio(self, audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
        """Translates an uploaded audio file into text (Speech-to-Text).
        Uses Google Cloud Speech-to-Text as primary, with Gemini Multimodal as fallback.
        """
        logger.info("Transcribing audio using Google Cloud Speech-to-Text...")
        if self.stt_client:
            try:
                from google.cloud import speech

                audio = speech.RecognitionAudio(content=audio_bytes)
                if "wav" in (mime_type or "").lower():
                    config = speech.RecognitionConfig(
                        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                        language_code="en-US",
                    )
                else:
                    config = speech.RecognitionConfig(
                        encoding=speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
                        language_code="en-US",
                    )

                response = self.stt_client.recognize(config=config, audio=audio)
                transcript = " ".join(
                    [result.alternatives[0].transcript for result in response.results if result.alternatives]
                )
                if transcript.strip():
                    logger.info(f"Google Cloud STT transcribed text: '{transcript}'")
                    return transcript.strip()
            except Exception as e:
                logger.warning(f"Google Cloud Speech-to-Text encountered an issue: {e}. Trying Gemini Multimodal...")

        # Fallback to Gemini Multimodal Audio transcription
        if self.model:
            try:
                from vertexai.generative_models import Part

                clean_mime = mime_type.split(";")[0].strip() if mime_type else "audio/webm"
                audio_part = Part.from_data(data=audio_bytes, mime_type=clean_mime)
                prompt = (
                    "Transcribe the spoken audio reply verbatim. "
                    "Return ONLY the transcribed text without quotes or explanations."
                )
                response = self.model.generate_content([audio_part, prompt])
                transcript = reply_text(response).strip()
                logger.info(f"Gemini Multimodal transcribed text: '{transcript}'")
                return transcript
            except Exception as e2:
                logger.error(f"Gemini transcription fallback error: {e2}")

        return ""

    def _classify_category_fallback(self, user_reply: str) -> str:
        """High-precision semantic and regex rule-based classifier.
        Used for instant sub-millisecond matching and when LLM is unavailable.
        """
        clean = (user_reply or "").strip().lower()
        if not clean:
            return "Other"

        # Direct exact match check
        for cat in VALID_CATEGORIES:
            if clean == cat.lower() or clean == f"category is {cat.lower()}":
                return cat

        # Pattern-based matching
        for cat, patterns in CATEGORY_RULES:
            for p in patterns:
                if re.search(p, clean, re.IGNORECASE):
                    return cat

        return "Other"

    def resolve_category(self, user_reply: str) -> str:
        """Translates casual user reply into a strict database category.
        Uses fast deterministic rule-based classifier first, then Gemini (`MODEL_LITE`) for complex replies.
        """
        if not user_reply or not user_reply.strip():
            return "Other"

        # 1. Fast path: deterministic semantic & keyword match (<1ms)
        fast_match = self._classify_category_fallback(user_reply)
        if fast_match != "Other" or user_reply.strip().lower() in ("other", "category is other"):
            logger.info(f"Fast semantic classifier resolved '{user_reply}' to: {fast_match}")
            return fast_match

        # 2. Try Gemini for nuanced / conversational descriptions
        if self.model:
            try:
                prompt = f"""
                The user was asked to categorize an expense or income transaction. Their reply was: "{user_reply}"
                Map this reply to exactly ONE of these valid database categories:
                [Food, Health/Medical, Home, Transportation, Personal, SIP, Utilities, Travel, Debt, Investment, Stocks, Rent, Savings, Paycheck, Bonus, Interest, Transfer, Other]

                Rules:
                - Return ONLY the exact category name from the list above.
                - Do not include punctuation, explanations, or quotes.
                - If the reply refers to internal transfer, account transfer, self transfer, or moving funds between accounts, map to 'Transfer'.
                - If the reply refers to food, groceries, dining, snacks, or restaurants, map to 'Food'.
                - If the reply refers to cab, taxi, metro, fuel, gas, or daily transit, map to 'Transportation'.
                - If the reply refers to vacation, hotel, flight, map to 'Travel'.
                - If unsure, return 'Other'.
                """
                response = self.model.generate_content(prompt)
                raw_category = reply_text(response).strip().strip("'\"").strip()

                for valid in VALID_CATEGORIES:
                    if raw_category.lower() == valid.lower():
                        logger.info(f"Gemini AI resolved '{user_reply}' to: {valid}")
                        return valid

            except Exception as e:
                logger.warning(
                    f"Gemini classification unavailable for '{user_reply}': {e}. "
                    f"Using fallback."
                )

        return fast_match

    @staticmethod
    def extract_payee_pattern(description: str) -> str:
        """Extracts a reusable payee/merchant pattern from a bank narration for learned rules.

        Examples:
          - 'UPI/RAHUL/778192837182/Transfer' -> 'UPI/RAHUL'
          - 'HungerBox [Kotak Statement] UPI/HungerBox/090508936480/Hungerbox Order UPI-613251143991' -> 'UPI/HUNGERBOX'
          - 'Electricity Bill [HDFC Statement] amazonpayhfc@yapl (Amazon Pay) (Ref: 612783646310)' -> 'AMAZONPAYHFC@YAPL'
          - 'UPI-ZOMATO ONLINE-998877665544' -> 'UPI-ZOMATO ONLINE'
          - 'POS 4111XXXX1111 APOLLO PHARMACY BLR' -> 'APOLLO PHARMACY'
        """
        raw = (description or "").strip()
        if not raw:
            return ""

        # Strip bracketed source tags such as [Kotak Statement], [HDFC Statement], [Kotak Alert]
        raw_no_tags = re.sub(r"\[[^\]]*(?:statement|alert)[^\]]*\]", " ", raw, flags=re.IGNORECASE)
        raw_no_tags = re.sub(r"\s+", " ", raw_no_tags).strip()

        # 1. Check for embedded slash-delimited rail narrations anywhere in the string (e.g. UPI/..., IMPS/..., NEFT/...)
        rail_match = re.search(
            r"\b(UPI|IMPS|NEFT|RTGS|ACH)/([^/|]+)(?:/([^/|]+))?",
            raw_no_tags,
            flags=re.IGNORECASE,
        )
        if rail_match:
            rail = rail_match.group(1).upper()
            seg1 = (rail_match.group(2) or "").strip()
            seg2 = (rail_match.group(3) or "").strip()
            for seg in (seg1, seg2):
                # Strip trailing UPI-123456789012 or (Ref: ...)
                clean_seg = re.sub(r"\bUPI-\d+\b.*$", "", seg, flags=re.IGNORECASE).strip()
                clean_seg = re.sub(r"\(Ref:.*$", "", clean_seg, flags=re.IGNORECASE).strip()
                if clean_seg and not clean_seg.isdigit():
                    return f"{rail}/{clean_seg.upper()}"

        # 2. Check for PCD/POS card merchant narration (e.g. PCD/8839/4531 DMART C21 MALL)
        pcd_match = re.search(
            r"\bPCD/\d+/([^/|]+)",
            raw_no_tags,
            flags=re.IGNORECASE,
        )
        if pcd_match:
            merch = re.sub(r"^\d+\s+", "", pcd_match.group(1).strip())
            merch = re.sub(r"\b\d{6,}\b", "", merch).strip(" -_/:*")
            if merch and not merch.isdigit():
                return merch[:40].upper()

        # 3. Check for UPI VPA handle (e.g. paytm-8774066@ptybl or amazonpayhfc@yapl)
        vpa_match = re.search(r"\b([a-zA-Z0-9.\-_]+@[a-zA-Z0-9]+)\b", raw_no_tags)
        if vpa_match:
            return vpa_match.group(1).upper()

        # 4. Handle slash-delimited narrations starting at the beginning
        if "/" in raw_no_tags:
            parts = [p.strip() for p in raw_no_tags.split("/") if p.strip()]
            if len(parts) >= 2 and parts[0].upper() in ("UPI", "IMPS", "NEFT", "RTGS", "ACH"):
                for seg in parts[1:]:
                    if not seg.isdigit():
                        return f"{parts[0].upper()}/{seg.upper()}"
            elif len(parts) >= 2 and not parts[0].upper().startswith("PCD"):
                return f"{parts[0].upper()}/{parts[1].upper()}"

        # 5. Handle hyphen-delimited narrations (e.g. UPI-RAHUL-7781...)
        if "-" in raw_no_tags:
            parts = [p.strip() for p in raw_no_tags.split("-") if p.strip()]
            if len(parts) >= 2 and parts[0].upper() in ("UPI", "IMPS", "NEFT", "RTGS", "ACH"):
                for seg in parts[1:]:
                    if not seg.isdigit():
                        return f"{parts[0].upper()}-{seg.upper()}"

        # Fallback: strip long reference digits, parenthetical refs, and masked card numbers
        cleaned = re.sub(r"\(Ref:\s*\d+\)", "", raw_no_tags, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b\d{4}[Xx\*]{4,8}\d{4}\b", "", cleaned)
        cleaned = re.sub(r"\b\d{6,}\b", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_/:")
        return (cleaned[:40] if cleaned else raw_no_tags[:40]).upper()