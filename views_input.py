import streamlit as st
import pandas as pd
from datetime import date
import time
import backend 
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode, JsCode

MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
MONTH_MAP = {m: i+1 for i, m in enumerate(MONTHS)}

# --- Helper: AgGrid 通用配置函数 ---
def make_aggrid(df, key, editable_cols=None, readonly_cols=None, dropdown_map=None, currency_cols=None):
    """
    df: Pandas DataFrame
    key: Unique key
    editable_cols: List of columns that are editable (if None, all editable except readonly)
    readonly_cols: List of columns that are strictly read-only
    dropdown_map: Dict { 'col_name': ['Option A', 'Option B'] }
    currency_cols: List of columns to format as currency ($)
    """
    gb = GridOptionsBuilder.from_dataframe(df)
    
    # 1. 全局配置：允许类似 Excel 的框选、多行复制
    gb.configure_default_column(
        groupable=True, 
        value=True, 
        enableRowGroup=True, 
        aggFunc='sum', 
        editable=True,
        resizable=True,
        filterable=True
    )
    gb.configure_selection('multiple', use_checkbox=True) # 允许勾选行
    gb.configure_grid_options(enableRangeSelection=True)  # 关键：开启 Excel 框选复制功能

    # 2. 字段特殊配置
    if readonly_cols:
        for col in readonly_cols:
            gb.configure_column(col, editable=False, cellStyle={'backgroundColor': '#f9f9f9', 'color': 'gray'})

    if dropdown_map:
        for col, options in dropdown_map.items():
            gb.configure_column(col, cellEditor='agSelectCellEditor', cellEditorParams={'values': options})

    if currency_cols:
        # JS 代码用于前端格式化显示金额
        js_currency_func = JsCode("""
        function(params) {
            if (params.value == null) return '';
            return '$' + params.value.toFixed(2).replace(/(\d)(?=(\d{3})+(?!\d))/g, '$1,');
        }
        """)
        for col in currency_cols:
            gb.configure_column(col, type=["numericColumn", "numberColumnFilter"], valueFormatter=js_currency_func)

    # 3. 构建 Grid
    gridOptions = gb.build()
    
    # 4. 渲染
    grid_response = AgGrid(
        df, 
        gridOptions=gridOptions, 
        height=500, 
        width='100%',
        data_return_mode=DataReturnMode.FILTERED_AND_SORTED, 
        update_mode=GridUpdateMode.MANUAL, # 只有点击保存或变更时才更新，防止刷新太快
        fit_columns_on_grid_load=True,
        allow_unsafe_jscode=True, # 允许运行上面的 JS 格式化代码
        key=key
    )
    
    return grid_response['data'] # 返回修改后的数据 (List of Dicts)

# --- Helper: 模拟获取 Compartments ---
def get_compartment_options(forest_id):
    return ["60810", "60812", "60814", "General"]

# --- 1. Log Sales Data (Transaction Level) ---
def view_log_sales():
    st.title("🚛 Log Sales Data (AgGrid Edition)")
    st.caption("✨ 支持 Ctrl+C/V 复制粘贴，像 Excel 一样操作。修改后请点击 'Save Transactions'。")
    
    forests = backend.get_forest_list()
    if not forests: return
    
    c1, c2 = st.columns([1, 2])
    with c1: 
        sel_forest = st.selectbox("Forest", [f['name'] for f in forests])
    fid = next(f['id'] for f in forests if f['name'] == sel_forest)
    
    # 获取基础配置数据
    products = backend.supabase.table("dim_products").select("*").execute().data
    product_codes = [p['grade_code'] for p in products] if products else []
    compartment_opts = get_compartment_options(fid) 
    
    # 获取现有数据
    res = backend.supabase.table("actual_sales_transactions").select("*").eq("forest_id", fid).order("date", desc=True).limit(50).execute()
    df = pd.DataFrame(res.data)
    
    # 初始化空行
    if df.empty: 
        df = pd.DataFrame([{
            "date": str(date.today()), 
            "ticket_number": "", 
            "compartment": compartment_opts[0], 
            "customer": "C001", 
            "market": "Export",
            "sale_type": "Purchase (Inv)", 
            "grade_code": "A", 
            "net_tonnes": 0.0, 
            "jas": 0.0, 
            "price": 0.0, 
            "levy_deduction": 0.0, 
            "total_value": 0.0
        }])
    else:
        # 预处理数据，保证 AgGrid 不报错
        if 'compartment' not in df.columns: df['compartment'] = compartment_opts[0]
        if 'sale_type' not in df.columns: df['sale_type'] = "Purchase (Inv)"
        if 'levy_deduction' not in df.columns: df['levy_deduction'] = 0.0
        # 必须确保 ID 列在，但可以隐藏或设为只读
        if 'id' not in df.columns: df['id'] = None

    # AgGrid 配置
    dropdowns = {
        "compartment": compartment_opts,
        "market": ["Export", "Domestic"],
        "sale_type": ["Purchase (Inv)", "Direct (Non-Inv)", "Adjustment"],
        "grade_code": product_codes
    }
    
    readonly = ["created_at", "forest_id", "grade_id"] # 这些列由系统维护，前端只读
    currency = ["price", "levy_deduction", "total_value"]
    
    # 渲染表格
    grid_data = make_aggrid(
        df, 
        key="ag_log_sales", 
        readonly_cols=readonly,
        dropdown_map=dropdowns,
        currency_cols=currency
    )

    if st.button("💾 Save Transactions"):
        df_edited = pd.DataFrame(grid_data) # 转回 DataFrame
        
        recs = []
        for _, row in df_edited.iterrows():
            # 逻辑处理：ID 匹配
            gid = next((p['id'] for p in products if p['grade_code'] == row.get('grade_code')), None)
            
            # 自动计算 Total Value (如果用户没填或填0)
            net_tonnes = float(row.get('net_tonnes') or 0)
            price = float(row.get('price') or 0)
            levy = float(row.get('levy_deduction') or 0)
            
            # 简单的计算逻辑
            calc_total = float(row.get('total_value') or 0)
            if calc_total == 0 and price != 0:
                calc_total = (net_tonnes * price) - levy

            record = {
                "forest_id": fid, 
                "date": str(row['date']), 
                "ticket_number": row.get('ticket_number'),
                "compartment": row.get('compartment'), 
                "sale_type": row.get('sale_type'),     
                "grade_id": gid, 
                "customer": row.get('customer'), 
                "market": row.get('market'),
                "net_tonnes": net_tonnes, 
                "jas": float(row.get('jas') or 0), 
                "price": price, 
                "levy_deduction": levy, 
                "total_value": calc_total
            }
            
            # 如果是更新现有行，带上 ID
            if row.get('id') and pd.notnull(row.get('id')):
                record['id'] = row['id']
                
            recs.append(record)

        try:
            backend.supabase.table("actual_sales_transactions").upsert(recs).execute()
            st.success("✅ Transactions Saved Successfully!")
            time.sleep(1)
            st.rerun()
        except Exception as e: st.error(f"Error: {e}")


# --- 2. Monthly Input (Updated with AgGrid) ---
def view_monthly_input(mode):
    st.title(f"📝 {mode} Planning (AgGrid)")
    forests = backend.get_forest_list()
    if not forests: return

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1: sel_forest = st.selectbox("Forest", [f['name'] for f in forests], key=f"f_{mode}")
    with c2: year = st.selectbox("Year", [2025, 2026], key=f"y_{mode}")
    with c3: month_str = st.selectbox("Month", MONTHS, key=f"m_{mode}")

    target_date = f"{year}-{MONTH_MAP[month_str]:02d}-01"
    fid = next(f['id'] for f in forests if f['name'] == sel_forest)
    
    if mode == "Budget":
        tabs = ["📋 Sales Forecast", "🚛 Log Transport & Volume", "💰 Operational & Harvesting"]
    else:
        tabs = ["🚛 Log Transport & Volume", "💰 Operational & Harvesting"]
    
    current_tabs = st.tabs(tabs)

    for i, tab_name in enumerate(tabs):
        with current_tabs[i]:
            
            # --- Tab A: Sales Forecast (Budget Only) ---
            if tab_name == "📋 Sales Forecast":
                df = backend.get_monthly_data("fact_production_volume", "dim_products", "grade_id", "grade_code", fid, target_date, mode, ['vol_tonnes', 'vol_jas', 'price_jas', 'amount'])
                
                # 重新排序列，隐藏 ID
                cols = ['grade_code', 'market', 'customer', 'vol_tonnes', 'vol_jas', 'price_jas', 'amount', 'grade_id']
                df = df[[c for c in cols if c in df.columns]]
                
                grid_data = make_aggrid(
                    df, 
                    key=f"ag_fc_{mode}",
                    readonly_cols=['grade_code', 'grade_id'],
                    dropdown_map={'market': ['Export', 'Domestic']},
                    currency_cols=['price_jas', 'amount']
                )
                
                if st.button("Save Forecast", key=f"b_ag_fc"):
                    edited_df = pd.DataFrame(grid_data)
                    if backend.save_monthly_data(edited_df, "fact_production_volume", "grade_id", fid, target_date, mode): 
                        st.success("Forecast Saved!")

            # --- Tab B: Transport & Volume ---
            elif tab_name == "🚛 Log Transport & Volume":
                 df = backend.get_monthly_data("fact_production_volume", "dim_products", "grade_id", "grade_code", fid, target_date, mode, ['vol_tonnes', 'vol_jas', 'price_jas', 'amount'])
                 
                 cols = ['grade_code', 'vol_tonnes', 'vol_jas', 'price_jas', 'amount', 'grade_id']
                 df = df[[c for c in cols if c in df.columns]]

                 grid_data = make_aggrid(
                     df, 
                     key=f"ag_vol_{mode}", 
                     readonly_cols=['grade_code', 'grade_id'],
                     currency_cols=['price_jas', 'amount']
                 )
                 
                 if st.button("Save Volume", key=f"b_ag_vol"):
                     edited_df = pd.DataFrame(grid_data)
                     if backend.save_monthly_data(edited_df, "fact_production_volume", "grade_id", fid, target_date, mode): st.success("Saved!")

            # --- Tab C: Operational Costs ---
            elif tab_name == "💰 Operational & Harvesting":
                 
                 # 1. 获取数据
                 df = backend.get_monthly_data("fact_operational_costs", "dim_cost_activities", "activity_id", "activity_name", fid, target_date, mode, ['quantity', 'unit_rate', 'total_amount'])
                 
                 # 2. Actual 模式下预填预算单价 (逻辑保持不变)
                 if mode == "Actual" and df['total_amount'].sum() == 0:
                     st.info("💡 系统已自动加载【预算单价】，请输入实际数量。")
                     df_budget = backend.get_monthly_data("fact_operational_costs", "dim_cost_activities", "activity_id", "activity_name", fid, target_date, "Budget", ['unit_rate', 'total_amount'])
                     
                     if not df_budget.empty:
                         bud_rate_map = df_budget.set_index('activity_id')['unit_rate'].to_dict()
                         for idx, row in df.iterrows():
                             bud_rate = bud_rate_map.get(row['activity_id'], 0.0)
                             if bud_rate > 0: df.at[idx, 'unit_rate'] = bud_rate

                 # 3. 整理列顺序
                 cols = ['activity_name', 'quantity', 'unit_rate', 'total_amount', 'activity_id']
                 df = df[[c for c in cols if c in df.columns]]

                 # 4. AgGrid
                 grid_data = make_aggrid(
                     df,
                     key=f"ag_cost_{mode}",
                     readonly_cols=['activity_name', 'activity_id'],
                     currency_cols=['unit_rate', 'total_amount']
                 )
                 
                 # 5. 保存
                 if st.button("Save Costs", key=f"b_ag_cost"):
                     edited_df = pd.DataFrame(grid_data)
                     # 简单的后端补算
                     for i, row in edited_df.iterrows():
                         t = float(row.get('total_amount') or 0)
                         q = float(row.get('quantity') or 0)
                         r = float(row.get('unit_rate') or 0)
                         if t == 0 and q > 0 and r > 0:
                             edited_df.at[i, 'total_amount'] = q * r
                             
                     if backend.save_monthly_data(edited_df, "fact_operational_costs", "activity_id", fid, target_date, mode): 
                         st.success("Costs Saved!")
                         time.sleep(1)
                         st.rerun()