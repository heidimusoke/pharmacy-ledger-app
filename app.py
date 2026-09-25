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
st.write("Scan a ledger photo, manually enter daily records, or edit existing sheet entries.")

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


def get_unique_dates(sheet):
    """Fetches all unique non-header dates recorded in Column A."""
    try:
        dates = sheet.col_values(1)
        unique_dates = []
        for d in dates:
            d_clean = d.strip()
            if d_clean and d_clean.lower() != "date" and d_clean not in unique_dates:
                unique_dates.append(d_clean)
        return list(reversed(unique_dates))
    except Exception:
        return []


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

    st.write(f"**Net Sale (Col E):** {net_sale:,} UGX")
    st.write(f"**Total Expense / Final Net (Col H):** {total_expense_desc:,} UGX")

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

                    # Structure matching columns A through I
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
tab1, tab2, tab3 = st.tabs(["📷 Upload & Scan", "✍️ Manual Entry", "✏️ Edit Records"])

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

    st.info(f"**Net Sale (Col E):** {net_sale:,} UGX | **Final Net (Col H):** {final_net:,} UGX")

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

# ==========================================
# TAB 3: EDIT EXISTING RECORDS
# ==========================================
with tab3:
    st.write("### Edit Existing Record")

    try:
        sheet = get_google_sheet()
        available_dates = get_unique_dates(sheet)
    except Exception as e:
        available_dates = []
        st.error(f"Error connecting to Google Sheets: {str(e)}")

    if not available_dates:
        st.info("No recorded entries found in the Google Sheet.")
    else:
        selected_edit_date = st.selectbox("Select Date to Edit", options=available_dates)

        if selected_edit_date:
            all_records = sheet.get_all_values()

            # Find row indices for selected date
            target_indices = []
            for idx, row in enumerate(all_records):
                if row and row[0].strip() == selected_edit_date:
                    target_indices.append(idx + 1)  # 1-based index for gspread

            # Reset edit overhead count state when switching dates
            if (
                "last_selected_date" not in st.session_state
                or st.session_state.last_selected_date != selected_edit_date
            ):
                st.session_state.last_selected_date = selected_edit_date
                st.session_state.edit_overhead_extra = 0

            # Collect existing blocks for Drugs, Cosmetics, and Overheads
            drugs_row_idx = None
            cosmetics_row_idx = None
            overhead_rows = []

            for r_idx in target_indices:
                row_data = all_records[r_idx - 1]
                category = row_data[1].strip() if len(row_data) > 1 else ""

                if category == "Drugs" and drugs_row_idx is None:
                    drugs_row_idx = r_idx
                elif category == "Cosmetics" and cosmetics_row_idx is None:
                    cosmetics_row_idx = r_idx

                exp_name = row_data[5].strip() if len(row_data) > 5 else ""
                exp_price = row_data[6].strip() if len(row_data) > 6 else ""
                if exp_name or exp_price:
                    overhead_rows.append((r_idx, exp_name, exp_price))

            # Fetch current Logger (Col I - index 8)
            edit_logged_by = ""
            if target_indices:
                first_row = all_records[target_indices[0] - 1]
                edit_logged_by = first_row[8] if len(first_row) > 8 else ""

            edit_logger = st.text_input(
                "Logged By", value=edit_logged_by, key=f"edit_logger_{selected_edit_date}"
            )

            st.markdown("---")
            st.write("💊 **Drugs**")
            d_row = all_records[drugs_row_idx - 1] if drugs_row_idx else []
            e_d_gross = st.text_input(
                "Drugs Gross", value=d_row[2] if len(d_row) > 2 else "0", key=f"e_dg_{selected_edit_date}"
            )
            e_d_exp = st.text_input(
                "Drugs Direct Expense", value=d_row[3] if len(d_row) > 3 else "0", key=f"e_de_{selected_edit_date}"
            )

            st.write("💄 **Cosmetics**")
            c_row = all_records[cosmetics_row_idx - 1] if cosmetics_row_idx else []
            e_c_gross = st.text_input(
                "Cosmetics Gross", value=c_row[2] if len(c_row) > 2 else "0", key=f"e_cg_{selected_edit_date}"
            )
            e_c_exp = st.text_input(
                "Cosmetics Direct Expense", value=c_row[3] if len(c_row) > 3 else "0", key=f"e_ce_{selected_edit_date}"
            )

            st.markdown("---")
            st.write("💸 **Overhead Expenses**")

            e_overheads = []
            for i, (r_i, name, price) in enumerate(overhead_rows):
                col_o1, col_o2 = st.columns(2)
                with col_o1:
                    o_n = st.text_input(
                        f"Expense #{i+1} Name", value=name, key=f"e_on_{selected_edit_date}_{i}"
                    )
                with col_o2:
                    o_p = st.text_input(
                        f"Expense #{i+1} Price", value=price, key=f"e_op_{selected_edit_date}_{i}"
                    )
                e_overheads.append((r_i, o_n, o_p))

            base_count = len(overhead_rows)
            for j in range(st.session_state.edit_overhead_extra):
                slot_num = base_count + j + 1
                col_o1, col_o2 = st.columns(2)
                with col_o1:
                    o_n = st.text_input(
                        f"Expense #{slot_num} Name", value="", key=f"e_on_extra_{selected_edit_date}_{j}"
                    )
                with col_o2:
                    o_p = st.text_input(
                        f"Expense #{slot_num} Price", value="0", key=f"e_op_extra_{selected_edit_date}_{j}"
                    )
                e_overheads.append((None, o_n, o_p))

            col_add_edit_exp, _ = st.columns([1, 2])
            with col_add_edit_exp:
                if st.button("➕ Add Overhead Expense Slot", key="btn_add_edit_exp"):
                    st.session_state.edit_overhead_extra += 1
                    st.rerun()

            st.markdown("---")

            p_d_gross = parse_amount(e_d_gross)
            p_d_exp = parse_amount(e_d_exp)

            p_c_gross = parse_amount(e_c_gross)
            p_c_exp = parse_amount(e_c_exp)

            p_overheads_total = sum(parse_amount(p) for _, _, p in e_overheads)
            recalc_net = (p_d_gross + p_c_gross) - (p_d_exp + p_c_exp)
            recalc_final_net = recalc_net - p_overheads_total

            st.info(f"**Updated Net Sale (Col E):** {recalc_net:,} UGX | **Final Net (Col H):** {recalc_final_net:,} UGX")

            if st.button("✏️ Save Edits to Google Sheets", type="primary"):
                edit_missing_fields = []

                if not edit_logger.strip():
                    edit_missing_fields.append("Logged By")

                if p_d_gross == 0 and p_c_gross == 0:
                    edit_missing_fields.append("At least one Gross Sale (Drugs or Cosmetics)")

                for i, (_, o_n, o_p) in enumerate(e_overheads):
                    parsed_p = parse_amount(o_p)
                    clean_n = o_n.strip()
                    if clean_n and parsed_p == 0:
                        edit_missing_fields.append(f"Price for Expense '{clean_n}'")
                    elif not clean_n and parsed_p > 0:
                        edit_missing_fields.append(f"Name for Expense #{i+1}")

                if edit_missing_fields:
                    st.error(
                        "⚠️ Cannot save changes with empty required fields:\n\n- "
                        + "\n- ".join(edit_missing_fields)
                    )
                else:
                    try:
                        with st.spinner("Updating Google Sheet record..."):
                            # Update Drugs Row (Col C=3, Col D=4, Col E=5, Col H=8, Col I=9)
                            if drugs_row_idx:
                                sheet.update_cell(drugs_row_idx, 3, p_d_gross)
                                sheet.update_cell(drugs_row_idx, 4, p_d_exp)
                                sheet.update_cell(drugs_row_idx, 5, recalc_net)
                                sheet.update_cell(drugs_row_idx, 8, recalc_final_net)
                                sheet.update_cell(drugs_row_idx, 9, edit_logger.strip())

                            # Update Cosmetics Row (Col C=3, Col D=4)
                            if cosmetics_row_idx:
                                sheet.update_cell(cosmetics_row_idx, 3, p_c_gross)
                                sheet.update_cell(cosmetics_row_idx, 4, p_c_exp)

                            # Handle existing overhead updates & new overhead additions
                            new_rows_to_add = []
                            for r_i, o_n, o_p in e_overheads:
                                clean_n = o_n.strip()
                                parsed_p = parse_amount(o_p)

                                if r_i is not None:
                                    sheet.update_cell(r_i, 6, clean_n)
                                    sheet.update_cell(r_i, 7, parsed_p if (clean_n or parsed_p > 0) else "")
                                else:
                                    if clean_n or parsed_p > 0:
                                        new_rows_to_add.append([
                                            "", "", "", "", "", clean_n, parsed_p, "", ""
                                        ])

                            if new_rows_to_add:
                                sheet.append_rows(new_rows_to_add, value_input_option="USER_ENTERED")

                        st.success(f"Record for {selected_edit_date} updated successfully!")
                        st.session_state.edit_overhead_extra = 0
                        st.rerun()

                    except Exception as update_err:
                        st.error(f"Failed to update entry: {str(update_err)}")