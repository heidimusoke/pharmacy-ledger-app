import json
import time
from google import genai
from google.genai import types
import gspread
from PIL import Image, ImageOps
import streamlit as st

# Page Configuration
st.set_page_config(
    page_title="Pharmacy Ledger Inserter", page_icon="📑", layout="centered"
)

st.title("📑 Pharmacy Ledger Inserter")
st.write("Upload or capture a photo of the daily logbook page.")

# Fetch Gemini API Key from Streamlit Secrets (or fallback for local testing)
GEMINI_API_KEY = st.secrets.get(
    "GEMINI_API_KEY",
    "AQ.Ab8RN6KhVtM3WvIqrMKVnT94EB2ZgmFG5KrvWXJ_WnlezYEh9Q",
)
client = genai.Client(api_key=GEMINI_API_KEY)


def get_google_sheet():
    """Connects to Google Sheets using credentials stored in Streamlit Secrets."""
    if "gcp_service_account" in st.secrets:
        # Production: Load JSON object directly from Streamlit Cloud Secrets
        creds_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(creds_dict)
    else:
        # Local fallback: Load local file
        gc = gspread.service_account(filename="credentials.json")
    return gc.open("Daily Records").sheet1


def get_last_sheet_date(sheet):
    """Reads Column A of the Google Sheet to find the last recorded date entry."""
    try:
        dates = sheet.col_values(1)
        for val in reversed(dates):
            val_clean = val.strip()
            if val_clean and val_clean.lower() != "date":
                return val_clean
    except Exception:
        pass
    return None


def get_working_models():
    """Dynamically discovers active Gemini models for your API key."""
    try:
        available_models = []
        for m in client.models.list():
            supported_methods = getattr(m, "supported_generation_methods", []) or []
            if "generateContent" in supported_methods or not supported_methods:
                available_models.append(m.name.replace("models/", ""))
        flash_models = [m for m in available_models if "flash" in m.lower()]
        other_models = [m for m in available_models if "flash" not in m.lower()]
        return flash_models + other_models
    except Exception:
        return ["gemini-2.5-flash", "gemini-2.0-flash"]


def preprocess_image(pil_img):
    """Corrects EXIF orientation and auto-rotates portrait images 90 degrees."""
    img = ImageOps.exif_transpose(pil_img)
    width, height = img.size
    if height > width:
        img = img.rotate(270, expand=True)
    return img


# Mobile File/Camera Uploader
uploaded_file = st.file_uploader(
    "Select ledger photo from Gallery or Camera",
    type=["jpg", "jpeg", "png"],
)

if uploaded_file is not None:
    raw_image = Image.open(uploaded_file)
    st.image(raw_image, caption="Uploaded Image", use_container_width=True)

    if st.button("🚀 Process & Append to Google Sheets", type="primary"):
        with st.spinner("Analyzing handwritten ledger entries..."):
            try:
                ledger_img = preprocess_image(raw_image)
                sheet = get_google_sheet()
                last_date = get_last_sheet_date(sheet)

                date_context_str = (
                    f"The last date recorded in the spreadsheet prior to this page was:"
                    f" {last_date}."
                    if last_date
                    else "No previous dates found in sheet."
                )

                prompt = f"""
                Analyze this handwritten pharmacy logbook image with high accuracy.
                {date_context_str}

                CRITICAL NUMBER EXTRACTION INSTRUCTIONS:
                - Take extra care reading digits (e.g., distinguish clearly between 0, 1, 6, 7, and 8).
                - Do not omit zeros at the end of amounts (e.g., read 33000 as 33000, not 3300).
                - Look closely at every row and column in the handwritten ledger.

                Extract EVERY daily entry shown in the image and return ONLY a valid JSON array matching this schema:

                [
                  {{
                    "date": "D/M/YYYY",
                    "sales": [
                      {{"category": "Drugs", "gross_sale": 416700, "cash_sale": 411100, "direct_expense": 5600}},
                      {{"category": "Cosmetics", "gross_sale": 33500, "cash_sale": 33500, "direct_expense": 0}}
                    ],
                    "net_sale": 356200,
                    "overhead_expenses": [
                      {{"expense_name": "Rent", "price": 33000}},
                      {{"expense_name": "Momo", "price": 35000}},
                      {{"expense_name": "Allow+AT", "price": 21000}}
                    ],
                    "logged_by": "FLAVIA"
                  }}
                ]

                EXTRACTION & DATE RULES:
                1. "date": Read handwritten date headers and format strictly as D/M/YYYY (Day/Month/Year, e.g., 8/11/2026 for 8th November 2026 or 11/8/2026 for 11th August 2026 depending on the log).
                2. Output strict valid JSON array only with no markdown wrapping.
                """

                models_to_try = get_working_models()
                response = None
                last_exception = None

                for model_name in models_to_try:
                    for attempt in range(3):
                        try:
                            response = client.models.generate_content(
                                model=model_name,
                                contents=[ledger_img, prompt],
                                config=types.GenerateContentConfig(
                                    response_mime_type="application/json"
                                ),
                            )
                            if response:
                                break
                        except Exception as err:
                            last_exception = err
                            if (
                                "503" in str(err)
                                or "429" in str(err)
                                or "UNAVAILABLE" in str(err)
                            ):
                                time.sleep(3 * (attempt + 1))
                            else:
                                break
                    if response:
                        break

                if not response:
                    raise last_exception

                records = json.loads(response.text)
                if isinstance(records, dict):
                    records = [records]

                rows_to_append = []
                for data in records:
                    sales = data.get("sales", [])
                    overheads = data.get("overhead_expenses", [])
                    max_rows = max(len(sales), len(overheads), 2)  # Ensure at least Drugs & Cosmetics rows

                    for i in range(max_rows):
                        # Row 1 headers metadata
                        row_date = data.get("date", "") if i == 0 else ""
                        row_net_sale = data.get("net_sale", "") if i == 0 else ""
                        row_logged_by = data.get("logged_by", "") if i == 0 else ""

                        # Sales line item columns
                        if i < len(sales):
                            cat = sales[i].get("category", "")
                            gross = sales[i].get("gross_sale", "")
                            cash = sales[i].get("cash_sale", gross)
                            direct = sales[i].get("direct_expense", "")
                        else:
                            cat = ""
                            gross = ""
                            cash = ""
                            direct = ""

                        # Overhead expenses columns
                        if i < len(overheads):
                            overhead_name = overheads[i].get("expense_name", "")
                            overhead_price = overheads[i].get("price", "")
                        else:
                            overhead_name = ""
                            overhead_price = ""

                        # Appends in exact visual column order matching Daily Records
                        rows_to_append.append([
                            row_date,        # Col A: Date
                            cat,             # Col B: Category (Drugs / Cosmetics)
                            gross,           # Col C: Gross Sale
                            cash,            # Col D: Cash / Gross
                            direct,          # Col E: Direct Expense
                            overhead_name,   # Col F: Expense Name
                            overhead_price,  # Col G: Expense Price
                            row_net_sale,    # Col H: Net Sale
                            row_logged_by    # Col I: Logged By
                        ])

                sheet.append_rows(rows_to_append, value_input_option="USER_ENTERED")
                st.success(
                    f"Successfully added {len(records)} daily records to Google Sheets!"
                )

            except Exception as e:
                st.error(f"Error processing image: {str(e)}")