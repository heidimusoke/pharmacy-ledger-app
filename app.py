import json
import streamlit as st
from PIL import Image
from google import genai
import gspread

st.set_page_config(page_title="Pharmacy Ledger Inserter", page_icon="📝", layout="centered")

st.title("📝 Pharmacy Ledger Inserter")
st.write("Upload or capture a photo of the daily logbook page.")

# ---------------------------------------------------------
# 1. AUTHENTICATION & SECRETS
# ---------------------------------------------------------
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error("Error loading GEMINI_API_KEY from secrets.")
    st.stop()

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
    
    date_context = st.text_input("Date Context (optional, e.g. Aug 2026):", "")
    date_context_str = f"Context date hint: {date_context}" if date_context else ""

    if st.button("Process Ledger Page", type="primary"):
        with st.spinner("Extracting handwritten ledger data via Gemini..."):
            
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

            # Explicit list using gemini-3.6-flash as primary
            MODEL_FALLBACKS = [
                "gemini-3.6-flash",
                "gemini-3.5-flash",
                "gemini-2.5-flash"
            ]
            
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
                    continue

            if response is None:
                st.error(f"All Gemini model fallbacks failed. Last error: {str(last_exception)}")
                st.stop()

            extracted_data = None
            try:
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
                st.stop()
            except Exception as e:
                st.error(f"An error occurred during extraction parsing: {str(e)}")
                st.stop()

# ---------------------------------------------------------
            # 4. GOOGLE SHEETS INSERTION LOGIC (MULTI-ROW LAYOUT)
            # ---------------------------------------------------------
            if extracted_data:
                try:
                    # Open exact sheet name "Daily Records"
                    sh = gc.open("Daily Records") 
                    worksheet = sh.sheet1

                    rows_to_append = []

                    for entry in extracted_data:
                        date_val = str(entry.get("date") or "")
                        logged_by = str(entry.get("logged_by") or "")
                        
                        try:
                            net_sale = int(float(entry.get("net_sale") or 0))
                        except (ValueError, TypeError):
                            net_sale = 0

                        # Extract Sales Categories (Drugs, Cosmetics)
                        sales_list = entry.get("sales") if isinstance(entry.get("sales"), list) else []
                        drugs_item = {}
                        cosmetics_item = {}
                        
                        for item in sales_list:
                            if not isinstance(item, dict):
                                continue
                            cat = str(item.get("category") or "").lower()
                            if "drug" in cat:
                                drugs_item = item
                            elif "cos" in cat:
                                cosmetics_item = item

                        def clean_num(val):
                            try:
                                return int(float(val))
                            except (ValueError, TypeError):
                                return ""

                        # Extract Overhead Expenses list
                        overhead_list = entry.get("overhead_expenses") if isinstance(entry.get("overhead_expenses"), list) else []
                        
                        # Calculate maximum vertical rows needed for this entry (at least 2 for Drugs + Cosmetics)
                        total_sub_rows = max(2, len(overhead_list))

                        for i in range(total_sub_rows):
                            # Col 1: Date (Only on Row 1)
                            c_date = date_val if i == 0 else ""
                            
                            # Cols 2-5: Sales Data
                            if i == 0:
                                c_cat = "Drugs"
                                c_gross = clean_num(drugs_item.get("gross_sale"))
                                c_cash = clean_num(drugs_item.get("cash_sale") or drugs_item.get("gross_sale"))
                                c_exp = clean_num(drugs_item.get("direct_expense"))
                            elif i == 1:
                                c_cat = "Cosmetics"
                                c_gross = clean_num(cosmetics_item.get("gross_sale"))
                                c_cash = clean_num(cosmetics_item.get("cash_sale") or cosmetics_item.get("gross_sale"))
                                c_exp = clean_num(cosmetics_item.get("direct_expense"))
                            else:
                                c_cat = ""
                                c_gross = ""
                                c_cash = ""
                                c_exp = ""

                            # Cols 6-7: Overhead Expense Name & Price
                            if i < len(overhead_list) and isinstance(overhead_list[i], dict):
                                c_exp_name = str(overhead_list[i].get("expense_name") or "").strip().title()
                                c_exp_price = clean_num(overhead_list[i].get("price"))
                            else:
                                c_exp_name = ""
                                c_exp_price = ""

                            # Col 8: Net Sale (Only on Row 1)
                            c_netsale = net_sale if i == 0 else ""

                            # Col 9: Logged By (Only on Row 1)
                            c_logged = logged_by if i == 0 else ""

                            sub_row = [
                                c_date,
                                c_cat,
                                c_gross,
                                c_cash,
                                c_exp,
                                c_exp_name,
                                c_exp_price,
                                c_netsale,
                                c_logged
                            ]
                            rows_to_append.append(sub_row)

                    if rows_to_append:
                        worksheet.append_rows(rows_to_append, value_input_option="USER_ENTERED")
                        st.success("Successfully appended multi-row ledger entry to Daily Records!")

                except gspread.exceptions.SpreadsheetNotFound:
                    st.error("Spreadsheet 'Daily Records' not found! Double check exact casing and service account access.")
                except Exception as sheet_err:
                    if hasattr(sheet_err, "response"):
                        st.error(f"Google Sheets API Error ({sheet_err.response.status_code}): {sheet_err.response.text}")
                    else:
                        st.error(f"Error appending to Google Sheets: {repr(sheet_err)}")