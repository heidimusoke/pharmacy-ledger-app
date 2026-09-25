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
st.write("Scan a ledger photo or manually enter daily pharmacy records directly into Google Sheets.")

# Fetch Gemini API Key from Streamlit Secrets
GEMINI_API_KEY = st.secrets.get(
    "GEMINI_API_KEY",
    "AQ.Ab8RN6KhVtM3WvIqrMKVnT94EB2ZgmFG5KrvWXJ_WnlezYEh9Q",
)
client = genai.Client(api_key=GEMINI_API_KEY)


def get_google_sheet():
    """Connects to Google Sheets using credentials stored in Streamlit Secrets."""
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(creds_dict)
    else:
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


def parse_amount(val):
    """Safely converts input string to integer."""
    try:
        return int(str(val).replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0


# Confirmation Modal Dialog for Manual Submission
@st.dialog("Confirm Data Submission")
def confirm_and_submit_dialog(
    formatted_date,
    logged_by,
    drugs_gross,
    drugs_expense,
    cosmetics_gross,
    cosmetics_expense,
    valid_overheads,
    net_sale,
    total_expense_desc,
):
    st.write("Please review the details before inserting into Google Sheets:")

    st.markdown(f"**Date:** {formatted_date}")
    st.markdown(f"**Logged By:** {logged_by or 'N/A'}")

    st.markdown("---")
    st.markdown("**Sales Summary:**")
    st.dataframe(
        [
            {
                "Category": "Drugs",
                "Gross Sale": f"{drugs_gross:,}",
                "Direct Expense": f"{drugs_expense:,}",
            },
            {
                "Category": "Cosmetics",
                "Gross Sale": f"{cosmetics_gross:,}",
                "Direct Expense": f"{cosmetics_expense:,}",
            },
        ],
        hide_index=True,
    )

    if valid_overheads:
        st.markdown("**Overhead Expenses:**")
        st.dataframe(
            [{"Expense Name": name, "Price": f"{price:,}"} for name, price in valid_overheads],
            hide_index=True,
        )

    st.write(f"**Net Sale:** {net_sale:,} UGX")
    st.write(f"**Final Net:** {total_expense_desc:,} UGX")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ Confirm & Insert", type="primary", use_container_width=True):
            try:
                sheet = get_google_sheet()
                max_rows = max(2, len(valid_overheads), 4)
                manual_rows = []

                for i in range(max_rows):
                    row_d = formatted_date if i == 0 else ""
                    row_net = net_sale if i == 0 else ""
                    row_desc = total_expense_desc if i == 0 else ""
                    row_log = logged_by.strip() if i == 0 else ""

                    if i == 0:
                        cat, gross, direct = "Drugs", drugs_gross or "", drugs_expense or ""
                    elif i == 1:
                        cat, gross, direct = "Cosmetics", cosmetics_gross or "", cosmetics_expense or ""
                    else:
                        cat, gross, direct = "", "", ""

                    if i < len(valid_overheads):
                        exp_name, exp_price = valid_overheads[i]
                    else:
                        exp_name, exp_price = "", ""

                    manual_rows.append([
                        row_d,
                        cat,
                        gross,
                        direct,
                        row_net,
                        exp_name,
                        exp_price,
                        row_desc,
                        row_log,
                    ])

                sheet.append_rows(manual_rows, value_input_option="USER_ENTERED")
                st.success(f"Successfully added manual record for {formatted_date}!")
                st.rerun()

            except Exception as err:
                st.error(f"Error submitting manual entry: {str(err)}")

    with col2:
        if st.button("❌ Cancel", use_container_width=True):
            st.rerun()


# Navigation Tabs
tab1, tab2 = st.tabs(["📷 Upload & Scan", "✍️ Manual Entry"])

# ==========================================
# TAB 1: UPLOAD & SCAN VIA GEMINI
# ==========================================
with tab1:
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
                        f"The last date recorded in the spreadsheet prior to this page was: {last_date}."
                        if last_date
                        else "No previous dates found in sheet."
                    )

                    prompt = f"""
                    Analyze this handwritten pharmacy logbook image with high accuracy.
                    {date_context_str}

                    CRITICAL NUMBER EXTRACTION INSTRUCTIONS:
                    - Take extra care reading digits (e.g., distinguish clearly between 0, 1, 6, 7, and 8).
                    - Do not omit zeros at the end of amounts (e.g., read 33000 as 33000, not 3300).

                    Extract EVERY daily entry shown in the image and return ONLY a valid JSON array matching this schema:

                    [
                      {{
                        "date": "D/M/YYYY",
                        "sales": [
                          {{"category": "Drugs", "gross_sale": 405900, "direct_expense": 397000}},
                          {{"category": "Cosmetics", "gross_sale": 44000, "direct_expense": 44000}}
                        ],
                        "net_sale": 8200,
                        "overhead_expenses": [
                          {{"expense_name": "Airtime", "price": 1000}},
                          {{"expense_name": "MOMO", "price": 30500}},
                          {{"expense_name": "Rent", "price": 33000}},
                          {{"expense_name": "Allowance", "price": 20000}}
                        ],
                        "total_expense_desc": 365400,
                        "logged_by": "Flavia"
                      }}
                    ]

                    EXTRACTION & DATE RULES:
                    1. "date": Read handwritten date headers and format strictly as D/M/YYYY (Day/Month/Year).
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
                        max_rows = max(len(sales), len(overheads), 4)

                        for i in range(max_rows):
                            row_date = data.get("date", "") if i == 0 else ""
                            row_net_sale = data.get("net_sale", "") if i == 0 else ""
                            row_desc = data.get("total_expense_desc", "") if i == 0 else ""
                            row_logged_by = data.get("logged_by", "") if i == 0 else ""

                            if i < len(sales):
                                cat = sales[i].get("category", "")
                                gross = sales[i].get("gross_sale", "")
                                direct = sales[i].get("direct_expense", "")
                            else:
                                cat, gross, direct = "", "", ""

                            if i < len(overheads):
                                overhead_name = overheads[i].get("expense_name", "")
                                overhead_price = overheads[i].get("price", "")
                            else:
                                overhead_name, overhead_price = "", ""

                            rows_to_append.append([
                                row_date,
                                cat,
                                gross,
                                direct,
                                row_net_sale,
                                overhead_name,
                                overhead_price,
                                row_desc,
                                row_logged_by,
                            ])

                    sheet.append_rows(rows_to_append, value_input_option="USER_ENTERED")
                    st.success(
                        f"Successfully added {len(records)} daily records to Google Sheets!"
                    )

                except Exception as e:
                    st.error(f"Error processing image: {str(e)}")

# ==========================================
# TAB 2: MANUAL ENTRY FORM
# ==========================================
with tab2:
    st.write("### Manual Ledger Entry")

    if "expense_count" not in st.session_state:
        st.session_state.expense_count = 1

    col_date, col_logger = st.columns(2)
    with col_date:
        entry_date = st.date_input("Date", format="DD/MM/YYYY")
    with col_logger:
        logged_by = st.text_input("Logged By", placeholder="e.g. Flavia")

    st.markdown("---")
    st.write("💊 **Drugs**")
    d_col1, d_col2 = st.columns(2)
    with d_col1:
        drugs_gross_raw = st.text_input("Drugs Gross Sale", value="0")
    with d_col2:
        drugs_expense_raw = st.text_input("Drugs Direct Expense", value="0")

    st.write("💄 **Cosmetics**")
    c_col1, c_col2 = st.columns(2)
    with c_col1:
        cosmetics_gross_raw = st.text_input("Cosmetics Gross Sale", value="0")
    with c_col2:
        cosmetics_expense_raw = st.text_input("Cosmetics Direct Expense", value="0")

    st.markdown("---")
    st.write("💸 **Overhead Expenses**")

    overheads_list = []
    for idx in range(st.session_state.expense_count):
        e_col1, e_col2 = st.columns(2)
        with e_col1:
            exp_name = st.text_input(
                f"Expense {idx+1} Name", key=f"exp_name_{idx}", placeholder="e.g. Rent"
            )
        with e_col2:
            exp_price_raw = st.text_input(
                f"Expense {idx+1} Price", key=f"exp_price_{idx}", value="0"
            )
        overheads_list.append((exp_name, exp_price_raw))

    col_add_exp, _ = st.columns([1, 2])
    with col_add_exp:
        if st.button("➕ Add Extra Expense Slot"):
            st.session_state.expense_count += 1
            st.rerun()

    st.markdown("---")

    drugs_gross = parse_amount(drugs_gross_raw)
    drugs_expense = parse_amount(drugs_expense_raw)

    cosmetics_gross = parse_amount(cosmetics_gross_raw)
    cosmetics_expense = parse_amount(cosmetics_expense_raw)

    parsed_overheads = [
        (name.strip(), parse_amount(price_str)) for name, price_str in overheads_list
    ]

    total_gross = drugs_gross + cosmetics_gross
    total_direct_exp = drugs_expense + cosmetics_expense
    net_sale = total_gross - total_direct_exp
    total_overheads = sum(p for _, p in parsed_overheads)
    final_net = net_sale - total_overheads

    st.info(f"**Net Sale:** {net_sale:,} UGX | **Final Net:** {final_net:,} UGX")

    if st.button("📌 Save Entry to Google Sheets", type="primary"):
        missing_fields = []

        if not logged_by.strip():
            missing_fields.append("Logged By")

        if drugs_gross == 0 and cosmetics_gross == 0:
            missing_fields.append("At least one Gross Sale (Drugs or Cosmetics)")

        for idx, (name, price) in enumerate(parsed_overheads):
            if name and price == 0:
                missing_fields.append(f"Price for Expense '{name}'")
            elif not name and price > 0:
                missing_fields.append(f"Name for Expense #{idx+1}")

        if missing_fields:
            st.error(
                "⚠️ Please fill in all required fields before submitting:\n\n- "
                + "\n- ".join(missing_fields)
            )
        else:
            formatted_date = entry_date.strftime("%d/%m/%Y").lstrip("0").replace("/0", "/")
            valid_overheads = [
                (name, price if price > 0 else "")
                for name, price in parsed_overheads
                if name or price > 0
            ]

            confirm_and_submit_dialog(
                formatted_date=formatted_date,
                logged_by=logged_by,
                drugs_gross=drugs_gross,
                drugs_expense=drugs_expense,
                cosmetics_gross=cosmetics_gross,
                cosmetics_expense=cosmetics_expense,
                valid_overheads=valid_overheads,
                net_sale=net_sale,
                total_expense_desc=final_net,
            )