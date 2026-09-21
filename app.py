import json
import streamlit as st
from PIL import Image
from google import genai
import gspread

# Page Config
st.set_page_config(page_title="Pharmacy Ledger Inserter", page_icon="📝", layout="centered")

st.title("📝 Pharmacy Ledger Inserter")
st.write("Upload or capture a photo of the daily logbook page.")

# ---------------------------------------------------------
# 1. AUTHENTICATION & SECRETS
# ---------------------------------------------------------
# Fetch Gemini API Key securely from Streamlit secrets
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error("Error loading GEMINI_API_KEY from secrets. Please check Advanced Settings.")
    st.stop()

# Connect to Google Sheets via Service Account Secrets
try:
    gcp_credentials = dict(st.secrets["gcp_service_account"])
    gc = gspread.service_account_from_dict(gcp_credentials)
except Exception as e:
    st.error("Error initializing Google Sheets service account from secrets.")
    st.stop()

# ---------------------------------------------------------
# 2. IMAGE INPUT
# ---------------------------------------------------------
uploaded_file = st.file_uploader(
    "Select ledger photo from Gallery or Camera", 
    type=["jpg", "jpeg", "png"]
)

if uploaded_file is not None:
    raw_image = Image.open(uploaded_file)
    st.image(raw_image, caption="Uploaded Image", use_container_width=True)
    
    # Optional date context to aid extraction
    date_context = st.text_input("Date Context (optional, e.g. Aug 2026):", "")
    date_context_str = f"Context date hint: {date_context}" if date_context else ""

    if st.button("Process Ledger Page", type="primary"):
        with st.spinner("Extracting handwritten ledger data via Gemini..."):
            
            # ---------------------------------------------------------
            # 3. GENERALIZED GEMINI PROMPT
            # ---------------------------------------------------------
            prompt = f"""
            Analyze this pharmacy ledger image containing handwritten daily entries.
            {date_context_str}

            Extract EVERY daily entry shown in the image and return ONLY a valid JSON array matching this generic schema:

            [
              {{
                "date": "M/D/YYYY",
                "sales": [
                  {{"category": "Category Name", "gross_sale": 0, "direct_expense": 0}}
                ],
                "net_sale": 0,
                "overhead_expenses": [
                  {{"expense_name": "Expense Name", "price": 0}}
                ],
                "total_overhead_expense": 0,
                "logged_by": "Name or Initials"
              }}
            ]

            EXTRACTION & SPATIAL ALIGNMENT RULES:
            1. "date": Read the date header at the top-left of the entry.
            2. "sales": Map each category row explicitly horizontally (Category -> Gross -> Direct Expense):
               - Column 1: Category Name (e.g., Drugs, Cosmetics).
               - Column 2: Gross Sale for that specific category.
               - Column 3: Direct Expense on that SAME category line. Do NOT shift a category's gross sale into another row's direct expense.
            3. "net_sale": Extract the net sales calculation total.
            4. "overhead_expenses": Extract all operational expenses (e.g., Rent, Allowance, Airtime) line-by-line with their respective costs.
            5. "total_overhead_expense": Extract the grand total at the bottom of the overhead section.
            6. "logged_by": Extract any signature, name, or initials written at the bottom.
            7. Extract pure numbers for all monetary amounts (strip commas, spaces, or currency markers).
            8. Return strict, valid JSON format only without markdown formatting code blocks.
            """

            # List of candidate models in order of priority
            MODEL_FALLBACKS = ["gemini-3.6-flash", "gemini-1.5-flash", "gemini-2.5-flash"][cite: 5]
            response = None
            last_exception = None

            for model_name in MODEL_FALLBACKS:
                try:
                    response = gemini_client.models.generate_content(
                        model=model_name,
                        contents=[raw_image, prompt]
                    )
                    st.info(f"Successfully processed using `{model_name}`")
                    break
                except Exception as model_err:
                    last_exception = model_err
                    continue  # Try next model in fallback list

            if response is None:
                st.error(f"All Gemini model fallbacks failed. Last error: {str(last_exception)}")
                st.stop()

            try:
                # Parse JSON output
                response_text = response.text.strip()
                if response_text.startswith("```json"):
                    response_text = response_text.split("```json")[1].split("```")[0].strip()
                elif response_text.startswith("```"):
                    response_text = response_text.split("```")[1].split("```")[0].strip()
                    
                extracted_data = json.loads(response_text)
                
                st.success("Data successfully extracted!")
                st.json(extracted_data)

            except json.JSONDecodeError:
                st.error("Failed to parse JSON response from Gemini. Raw output:")
                st.code(response.text)
            except Exception as e:
                st.error(f"An error occurred during processing: {str(e)}")