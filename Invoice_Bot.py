import streamlit as st
import pandas as pd
import time
import random
from supabase import create_client

# --- 1. 配置 ---
st.set_page_config(page_title="🧾 Invoice 3rd Party Check", layout="wide")

st.markdown("""
<style>
    .match { color: green; font-weight: bold; }
    .mismatch { color: red; font-weight: bold; }
    .stDataFrame { border: 1px solid #ccc; }
</style>
""", unsafe_allow_html=True)

# 独立连接 Supabase (复用 secrets)
@st.cache_resource
def init_connection():
    try:
        return create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])
    except: return None

supabase = init_connection()

# --- 2. 模拟 AI 识别 (Mock OCR) ---
def mock_extract_invoice_data(file_obj):
    """
    模拟调用 Gemini/GPT 读取 PDF。
    在真实场景中，这里会调用 google-generativeai 库。
    """
    time.sleep(1.5) # 假装在思考
    
    # 随机生成一些识别结果用于演示
    filename = file_obj.name
    predicted_amount = random.randint(1000, 20000)
    vendor = "Unknown"
    
    if "Road" in filename: vendor = "Road Maintenance"
    elif "Harv" in filename: vendor = "Groundbase Harvesting"
    elif "Truck" in filename: vendor = "Cartage"
    
    return {
        "filename": filename,
        "vendor_detected": vendor,
        "invoice_no": f"INV-{random.randint(10000,99999)}",
        "date_detected": "2025-01-15",
        "amount_detected": float(predicted_amount)
    }

# --- 3. 界面逻辑 ---
st.title("🤖 3rd Party Invoice Reconciliation Bot")
st.caption("Upload contractor invoices (PDF) to verify against Actual Costs in ERP.")

col_upload, col_review = st.columns([1, 2])

with col_upload:
    st.subheader("1. Upload Invoices")
    uploaded_files = st.file_uploader("Drag PDFs here", type=["pdf"], accept_multiple_files=True)
    
    if uploaded_files:
        st.success(f"Loaded {len(uploaded_files)} files.")
        if st.button("🚀 Start AI Analysis"):
            results = []
            progress_bar = st.progress(0)
            
            for i, file in enumerate(uploaded_files):
                data = mock_extract_invoice_data(file)
                results.append(data)
                progress_bar.progress((i + 1) / len(uploaded_files))
            
            st.session_state['ocr_results'] = results
            st.success("Analysis Complete!")

with col_review:
    st.subheader("2. Reconciliation Review")
    
    if 'ocr_results' in st.session_state:
        results = st.session_state['ocr_results']
        
        # 准备对比表格
        reconcile_data = []
        
        for item in results:
            # 1. 尝试去数据库找匹配的费用
            # 这里简单匹配: 找 2025-01-01 的 Actual Cost，且 Activity Name 包含识别出的 Vendor
            match_status = "❌ Not Found"
            db_amount = 0
            diff = 0
            
            if supabase:
                # 查询 dim_cost_activities 获取 ID
                acts = supabase.table("dim_cost_activities").select("id").ilike("activity_name", f"%{item['vendor_detected']}%").execute().data
                if acts:
                    act_id = acts[0]['id']
                    # 查询 fact_operational_costs
                    costs = supabase.table("fact_operational_costs").select("total_amount")\
                        .eq("activity_id", act_id)\
                        .eq("month", "2025-01-01")\
                        .eq("record_type", "Actual").execute().data
                    
                    if costs:
                        db_amount = costs[0]['total_amount']
                        diff = item['amount_detected'] - db_amount
                        if abs(diff) < 1.0: match_status = "✅ Match"
                        else: match_status = "⚠️ Variance"
            
            reconcile_data.append({
                "Invoice File": item['filename'],
                "Vendor (AI)": item['vendor_detected'],
                "Inv #": item['invoice_no'],
                "Inv Amount": item['amount_detected'],
                "ERP Amount": db_amount,
                "Diff": diff,
                "Status": match_status
            })
            
        df_rec = pd.DataFrame(reconcile_data)
        
        # 样式化显示
        st.dataframe(
            df_rec.style.applymap(
                lambda x: 'color: red' if 'Variance' in str(x) else ('color: green' if 'Match' in str(x) else ''),
                subset=['Status']
            ),
            use_container_width=True
        )
        
        st.info("⚠️ 'Variance' indicates the invoice amount differs from what was entered in the Actuals Entry page.")