import streamlit as st
import pandas as pd
from supabase import create_client
import google.generativeai as genai
import json
import time
import re
from datetime import date

# --- A. 数据库连接 ---
@st.cache_resource
def init_connection():
    try:
        if "supabase" in st.secrets:
            return create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])
    except: return None
    return None

supabase = init_connection()

# --- B. Google AI 检查 ---
def check_google_key():
    return "google" in st.secrets and "api_key" in st.secrets["google"]

# --- C. 核心数据函数 ---
def get_forest_list():
    if not supabase: return []
    try: return supabase.table("dim_forests").select("*").execute().data
    except: return []

def get_monthly_data(table_name, dim_table, dim_id_col, dim_name_col, forest_id, target_date, record_type, value_cols):
    if not supabase: return pd.DataFrame()
    dims = supabase.table(dim_table).select("*").execute().data
    df_dims = pd.DataFrame(dims)
    if df_dims.empty: return pd.DataFrame()
    
    if dim_name_col not in df_dims.columns and 'activity_name' in df_dims.columns:
        df_dims[dim_name_col] = df_dims['activity_name']

    try:
        res = supabase.table(table_name).select("*").eq("forest_id", forest_id).eq("record_type", record_type).eq("month", target_date).execute()
        df_facts = pd.DataFrame(res.data)
    except: df_facts = pd.DataFrame()
    
    if df_facts.empty:
        cols_to_keep = ['id', dim_name_col]
        if 'grade_code' in df_dims.columns: cols_to_keep.append('grade_code')
        df_merged = df_dims[cols_to_keep].rename(columns={'id': dim_id_col})
        for c in value_cols: df_merged[c] = 0.0
    else:
        df_merged = pd.merge(df_dims, df_facts, left_on='id', right_on=dim_id_col, how='left')
        for c in value_cols: df_merged[c] = df_merged[c].fillna(0.0)
    
    df_merged = df_merged.loc[:, ~df_merged.columns.duplicated()]
    df_merged = df_merged.reset_index(drop=True)

    if 'market' not in df_merged.columns and 'grade_code' in df_merged.columns:
        df_merged['market'] = df_merged['grade_code'].apply(lambda x: 'Domestic' if 'Domestic' in str(x) else 'Export')
    if 'customer' not in df_merged.columns: df_merged['customer'] = 'FCO' 
    return df_merged

def save_monthly_data(edited_df, table_name, dim_id_col, forest_id, target_date, record_type):
    if not supabase or edited_df.empty: return False
    records = []
    for _, row in edited_df.iterrows():
        # 安全处理：确保 ID 存在
        if dim_id_col not in row or pd.isna(row[dim_id_col]):
            continue

        rec = { 
            "forest_id": forest_id, 
            dim_id_col: int(row[dim_id_col]), 
            "month": target_date, 
            "record_type": record_type 
        }
        for col in row.index:
            if col in ['vol_tonnes', 'vol_jas', 'price_jas', 'amount', 'quantity', 'unit_rate', 'total_amount']:
                # 安全转换：将可能的字符串数字转为 float
                val = row[col]
                try:
                    val = float(val) if val is not None else 0.0
                except:
                    val = 0.0
                rec[col] = val
        records.append(rec)
    try:
        supabase.table(table_name).upsert(records, on_conflict=f"forest_id,{dim_id_col},month,record_type").execute()
        return True
    except Exception as e:
        print(f"Save Error: {e}")
        return False

# --- D. 发票 HTML 生成 ---
def generate_invoice_html(invoice_no, invoice_date, bill_to, month_str, year, items, subtotal, gst_val, total_due):
    rows_html = ""
    for item in items:
        rows_html += f"<tr class='item'><td>{item['desc']}</td><td class='text-right'>${item['amount']:,.2f}</td></tr>"
    return f"""
    <!DOCTYPE html>
    <html><head><style>body {{ font-family: Arial; padding: 20px; }} .invoice-box {{ max-width: 800px; margin: auto; border: 1px solid #eee; padding: 30px; }} table {{ width: 100%; }} .text-right {{ text-align: right; }} .item td {{ border-bottom: 1px solid #eee; }} .total td {{ border-top: 2px solid #eee; font-weight: bold; }}</style></head><body><div class="invoice-box"><table><tr><td><h1>INVOICE</h1></td><td class="text-right">#{invoice_no}<br>{invoice_date}</td></tr><tr><td><strong>FCO Management</strong></td><td class="text-right"><strong>Bill To:</strong><br>{bill_to}</td></tr>{rows_html}<tr class="total"><td></td><td class="text-right">Total: ${total_due:,.2f}</td></tr></table></div></body></html>
    """

# --- E. AI 识别核心逻辑 ---
def real_extract_invoice_data(file_obj):
    try:
        if not check_google_key():
            return [{"vendor_detected": "Error", "error_msg": "API Key missing", "amount_detected": 0, "filename": file_obj.name}]

        genai.configure(api_key=st.secrets["google"]["api_key"])
        try:
            model = genai.GenerativeModel('gemini-2.0-flash') 
        except:
            model = genai.GenerativeModel('gemini-1.5-flash')
        
        file_obj.seek(0)
        file_bytes = file_obj.read()
        
        prompt_text = """
        Analyze this PDF file. It contains MULTIPLE distinct invoices.
        Extract ALL invoices found into a single JSON ARRAY.
        For each invoice, summarize the work done into a short "description".
        Output format:
        [
            {
                "vendor_detected": "Company A",
                "invoice_no": "INV-001",
                "invoice_date": "YYYY-MM-DD", 
                "amount_detected": 1000.00,
                "description": "Brief summary"
            }
        ]
        """
        
        response = model.generate_content([
            {'mime_type': 'application/pdf', 'data': file_bytes},
            prompt_text
        ])
        
        raw_text = response.text
        match = re.search(r'\[.*\]', raw_text, re.DOTALL)
        
        final_results = []
        
        if match:
            json_str = match.group(0)
            try:
                data_list = json.loads(json_str)
                if isinstance(data_list, dict): data_list = [data_list]
                    
                for item in data_list:
                    if not isinstance(item, dict): continue
                    item['filename'] = file_obj.name
                    if "amount_detected" not in item: item["amount_detected"] = 0.0
                    if "invoice_no" not in item: item["invoice_no"] = "Unknown"
                    if "vendor_detected" not in item: item["vendor_detected"] = "Unknown"
                    if "invoice_date" not in item: item["invoice_date"] = str(date.today())
                    if "description" not in item: item["description"] = "N/A"
                    
                    if isinstance(item["amount_detected"], str):
                        clean_amt = item["amount_detected"].replace('$','').replace(',','').strip()
                        try: item["amount_detected"] = float(clean_amt)
                        except: item["amount_detected"] = 0.0
                    
                    final_results.append(item)
                return final_results
            except json.JSONDecodeError:
                return [{"filename": file_obj.name, "vendor_detected": "Error", "error_msg": "JSON Parse Error", "amount_detected": 0}]
        else:
            return [{"filename": file_obj.name, "vendor_detected": "Error", "error_msg": "No JSON Array found", "amount_detected": 0}]

    except Exception as e:
        return [{"filename": file_obj.name, "vendor_detected": "Error", "error_msg": str(e), "amount_detected": 0}]

# --- F. 调试函数 ---
def list_available_models():
    genai.configure(api_key=st.secrets["google"]["api_key"])
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(m.name)

# --- G. GL Mapping Logic ---
def get_gl_mapping(forest_id):
    """
    获取指定林地的 GL 映射表
    """
    if not supabase: return {}, {}
    
    try:
        data = supabase.table("dim_gl_mappings").select("*").eq("forest_id", forest_id).execute().data
        
        cost_map = {}
        rev_map = {}
        
        for row in data:
            info = {'code': row['gl_code'], 'name': row['gl_name']}
            if row['item_type'] == 'Cost':
                cost_map[row['item_id']] = info
            elif row['item_type'] == 'Revenue':
                rev_map[row['item_id']] = info
                
        return cost_map, rev_map
    except Exception as e:
        print(f"Mapping Error: {e}")
        return {}, {}