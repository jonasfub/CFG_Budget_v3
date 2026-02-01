import streamlit as st
import pandas as pd
import time
import backend 

# --- 1. Invoice Bot ---
def view_invoice_bot():
    st.title("🤖 Invoice Bot (Audit & Archive)")
    
    if not backend.check_google_key():
        st.error("⚠️ Google API Key missing! Please check .streamlit/secrets.toml")
        return
    
    tab_audit, tab_archive = st.tabs(["🚀 Upload & Audit", "🗄️ Invoice Archive"])
    
    with tab_audit:
        # [Upload Section]
        st.subheader("1. Upload Invoices")
        uploaded_files = st.file_uploader("Drag PDFs here", type=["pdf"], accept_multiple_files=True)
        
        if uploaded_files:
            if st.button("🚀 Start AI Analysis", type="primary"):
                results = []
                progress_bar = st.progress(0)
                status_text = st.empty()
                total_files = len(uploaded_files)
                
                for i, file in enumerate(uploaded_files):
                    status_text.markdown(f"**Analyzing {i+1}/{total_files}:** `{file.name}`...")
                    
                    # 1. Backend Call
                    data_list = backend.real_extract_invoice_data(file)
                    
                    # 2. Re-attach file object
                    for item in data_list:
                        item['file_obj'] = file
                        
                    results.extend(data_list)
                    progress_bar.progress((i + 1) / total_files)
                
                progress_bar.progress(100)
                status_text.success("✅ Analysis Complete!")
                time.sleep(1)
                status_text.empty()
                progress_bar.empty()
                st.session_state['ocr_results'] = results

        st.divider()

        # [Review Section]
        st.subheader("2. Review & Archive Results")
        
        if 'ocr_results' in st.session_state:
            results = st.session_state['ocr_results']
            reconcile_data = []
            
            for i, item in enumerate(results):
                # Init Variables
                match_status = "❌ Not Found"
                db_amount = 0.0
                diff = 0.0
                
                if item.get("vendor_detected") == "Error":
                    match_status = "❌ AI Error"
                else:
                    # --- [关键修改] 增加 try-except 异常捕获 ---
                    try:
                        # Database Match
                        acts = backend.supabase.table("dim_cost_activities").select("id").ilike("activity_name", f"%{item['vendor_detected']}%").execute().data
                        if acts:
                            act_id = acts[0]['id']
                            costs = backend.supabase.table("fact_operational_costs").select("total_amount")\
                                .eq("activity_id", act_id).eq("record_type", "Actual").execute().data
                            
                            if costs:
                                db_amount = float(costs[0]['total_amount'])
                                diff = float(item['amount_detected']) - db_amount
                                if abs(diff) < 1.0: match_status = "✅ Match"
                                else: match_status = "⚠️ Variance"
                    except Exception as e:
                        # 如果数据库请求失败，记录错误但不崩溃
                        match_status = "⚠️ Net Error"
                        # 可选：在后台打印错误信息
                        print(f"Supabase connection error for {item.get('filename')}: {e}")
                    # ----------------------------------------

                reconcile_data.append({
                    "Select": False, "Index": i,
                    "File": item.get('filename'), 
                    "Vendor": item.get('vendor_detected'),
                    "Date": item.get('invoice_date'),
                    "Desc": item.get('description'),
                    "Inv #": item.get('invoice_no', ''), 
                    "Inv Amount": item.get('amount_detected', 0), 
                    "ERP Amount": db_amount, "Diff": diff, "Status": match_status
                })
            
            df_rec = pd.DataFrame(reconcile_data)
            
            if not df_rec.empty:
                # 类型转换，防止 date_editor 报错
                df_rec["Date"] = pd.to_datetime(df_rec["Date"], errors='coerce')
                df_rec["Inv Amount"] = df_rec["Inv Amount"].astype(float)
                df_rec["ERP Amount"] = df_rec["ERP Amount"].astype(float)
                df_rec["Diff"] = df_rec["Diff"].astype(float)

                edited_df = st.data_editor(
                    df_rec, 
                    column_config={
                        "Select": st.column_config.CheckboxColumn("Archive?", default=True), 
                        "Index": None,
                        "Date": st.column_config.DateColumn("Inv Date", format="YYYY-MM-DD"),
                        "Desc": st.column_config.TextColumn("Summary", width="medium"),
                        "Inv Amount": st.column_config.NumberColumn(format="$%.2f"),
                        "ERP Amount": st.column_config.NumberColumn(format="$%.2f"),
                        "Diff": st.column_config.NumberColumn(format="$%.2f"),
                    },
                    hide_index=True, width="stretch"
                )
                
                if st.button("💾 Confirm & Save"):
                    save_status = st.empty()
                    selected_rows = edited_df[edited_df["Select"] == True]
                    
                    if not selected_rows.empty:
                        save_status.info("Saving...")
                        for idx, row in selected_rows.iterrows():
                            try:
                                original_item = results[row['Index']]
                                file_obj = original_item['file_obj']
                                file_obj.seek(0)
                                
                                path = f"{int(time.time())}_{row['Index']}_{row['File']}"
                                backend.supabase.storage.from_("invoices").upload(path, file_obj.read(), {"content-type": "application/pdf"})
                                public_url = backend.supabase.storage.from_("invoices").get_public_url(path)
                                
                                backend.supabase.table("invoice_archive").insert({
                                    "invoice_no": row['Inv #'], 
                                    "vendor": row['Vendor'], 
                                    "invoice_date": str(row['Date'].date()) if pd.notnull(row['Date']) else None,
                                    "description": row['Desc'],        
                                    "amount": row['Inv Amount'],
                                    "file_name": row['File'], 
                                    "file_url": public_url, 
                                    "status": "Verified"
                                }).execute()
                            except Exception as e:
                                st.error(f"Error saving {row['File']}: {e}")
                        
                        save_status.success("Saved successfully!")
                    else:
                        st.warning("No invoices selected.")
        else:
            if uploaded_files: st.info("Click 'Start AI Analysis' above.")

    with tab_archive:
        st.subheader("🗄️ Invoice Digital Cabinet")
        search = st.text_input("Search Vendor/Invoice #")
        try:
            query = backend.supabase.table("invoice_archive").select("*").order("created_at", desc=True)
            if search: query = query.or_(f"vendor.ilike.%{search}%,invoice_no.ilike.%{search}%")
            res = query.execute().data
            if res:
                df_archive = pd.DataFrame(res)
                if "invoice_date" in df_archive.columns:
                     df_archive["invoice_date"] = pd.to_datetime(df_archive["invoice_date"], errors='coerce')

                st.dataframe(df_archive, column_config={
                    "file_url": st.column_config.LinkColumn("Link", display_text="Download"),
                    "amount": st.column_config.NumberColumn(format="$%.2f"),
                    "invoice_date": st.column_config.DateColumn("Date", format="YYYY-MM-DD")
                }, width="stretch", hide_index=True)
            else: st.info("No archives.")
        except Exception as e: st.error(f"Error loading archive: {e}")

# --- 2. Debug Models ---
def view_debug_models():
    st.title("🛠️ Google Model Debugger")
    if "google" not in st.secrets or "api_key" not in st.secrets["google"]:
        st.error("❌ Google API Key not found in secrets!")
        return

    import google.generativeai as genai
    genai.configure(api_key=st.secrets["google"]["api_key"])
    st.write("Checking available models...")
    try:
        models = list(genai.list_models())
        chat_models = [m for m in models if 'generateContent' in m.supported_generation_methods]
        st.success(f"✅ Found {len(chat_models)} models:")
        st.dataframe(pd.DataFrame([{"Model": m.name} for m in chat_models]), use_container_width=True)
    except Exception as e: st.error(f"❌ Connection Failed: {str(e)}")