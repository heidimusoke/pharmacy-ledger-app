# ---------------------------------------------------------
            # 4. GOOGLE SHEETS INSERTION LOGIC (FIXED)
            # ---------------------------------------------------------
            if extracted_data:
                try:
                    sh = gc.open("Pharmacy_Ledger_Workbook") 
                    worksheet = sh.sheet1

                    rows_to_append = []

                    for entry in extracted_data:
                        date_val = str(entry.get("date") or "")
                        
                        drugs_gross = 0
                        drugs_direct = 0
                        cosmetics_gross = 0
                        cosmetics_direct = 0
                        
                        sales_list = entry.get("sales") if isinstance(entry.get("sales"), list) else []
                        for item in sales_list:
                            if not isinstance(item, dict):
                                continue
                            cat = str(item.get("category") or "").lower()
                            gross_val = item.get("gross_sale") or 0
                            direct_val = item.get("direct_expense") or 0
                            
                            try:
                                gross_clean = int(float(gross_val))
                            except (ValueError, TypeError):
                                gross_clean = 0

                            try:
                                direct_clean = int(float(direct_val))
                            except (ValueError, TypeError):
                                direct_clean = 0
                            
                            if "drug" in cat:
                                drugs_gross = gross_clean
                                drugs_direct = direct_clean
                            elif "cos" in cat:
                                cosmetics_gross = gross_clean
                                cosmetics_direct = direct_clean

                        try:
                            net_sale = int(float(entry.get("net_sale") or 0))
                        except (ValueError, TypeError):
                            net_sale = 0

                        overhead_map = {}
                        overhead_list = entry.get("overhead_expenses") if isinstance(entry.get("overhead_expenses"), list) else []
                        for exp in overhead_list:
                            if not isinstance(exp, dict):
                                continue
                            name = str(exp.get("expense_name") or "").strip().lower()
                            p_val = exp.get("price") or 0
                            try:
                                price_clean = int(float(p_val))
                            except (ValueError, TypeError):
                                price_clean = 0
                            overhead_map[name] = price_clean
                        
                        rent = int(overhead_map.get("rent", 0))
                        allowance = int(overhead_map.get("allow", overhead_map.get("allowance", 0)))
                        airtime = int(overhead_map.get("airtime", 0))
                        
                        try:
                            total_overhead = int(float(entry.get("total_overhead_expense") or 0))
                        except (ValueError, TypeError):
                            total_overhead = 0

                        logged_by = str(entry.get("logged_by") or "")

                        # Construct sanitized row consisting purely of strings and integers
                        clean_row = [
                            date_val, 
                            "Drugs", drugs_gross, drugs_direct,
                            "Cosmetics", cosmetics_gross, cosmetics_direct,
                            net_sale, rent, allowance, airtime, total_overhead, logged_by
                        ]
                        rows_to_append.append(clean_row)

                    if rows_to_append:
                        # Append rows as a batch array
                        worksheet.append_rows(rows_to_append, value_input_option="USER_ENTERED")
                        st.success("Successfully appended ledger entry to Google Sheets!")

                except Exception as sheet_err:
                    # Unpack raw API response details if gspread throws a raw Response object
                    if hasattr(sheet_err, "response"):
                        st.error(f"Google Sheets API Error ({sheet_err.response.status_code}): {sheet_err.response.text}")
                    else:
                        st.error(f"Error appending to Google Sheets: {repr(sheet_err)}")