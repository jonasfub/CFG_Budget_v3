import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit.components.v1 as components
from datetime import date
import backend 
import time

MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
MONTH_MAP = {m: i+1 for i, m in enumerate(MONTHS)}

# --- 核心逻辑：发票上下文计算 ---
def calculate_invoice_context(df_sales, df_costs, mgmt_fee_pct):
    # A. 计算收入 (Credits) - 只计算 Sale Type 包含 'Purchase' 的项目 (F360买断/代售)
    total_revenue = 0.0
    if not df_sales.empty:
        # 确保 sale_type 列存在，防止旧数据报错
        if 'sale_type' in df_sales.columns:
            credits = df_sales[df_sales['sale_type'].str.contains("Purchase", na=False, case=False)]
            total_revenue = credits['total_value'].sum()
        else:
            total_revenue = df_sales['total_value'].sum() # 降级处理

    # B. 计算成本 (Debits)
    total_costs = df_costs['total_amount'].sum() if not df_costs.empty else 0.0
    
    # C. 计算管理费 (基于总成本)
    mgmt_fee_val = total_costs * (mgmt_fee_pct / 100)
    
    # D. 净额逻辑 (Costs + Fee - Revenue)
    # 正数 = CFGC 需要付钱; 负数 = F360 需要付钱给 CFGC
    subtotal = (total_costs + mgmt_fee_val) - total_revenue
    gst = subtotal * 0.15
    total_due = subtotal + gst
    
    return {
        "revenue": total_revenue,
        "costs": total_costs,
        "mgmt_fee": mgmt_fee_val,
        "subtotal_ex_gst": subtotal,
        "gst": gst,
        "total_due": total_due
    }

# --- 1. Dashboard (保持原有功能) ---
def view_dashboard():
    st.title("📊 Executive Dashboard")
    
    forests = backend.get_forest_list()
    if not forests: 
        st.warning("正在连接数据库或数据库为空...")
        return
    
    c1, c2 = st.columns([2, 1])
    with c1: 
        sel_forest = st.selectbox("Forest", ["ALL"] + [f['name'] for f in forests])
    with c2: 
        sel_year = st.selectbox("Year", [2025, 2026])
    
    try:
        # 简化的 Dashboard 逻辑，主要关注 Actual
        q_vol = backend.supabase.table("fact_production_volume").select("*").eq("record_type", "Actual")
        if sel_forest != "ALL":
            fid = next(f['id'] for f in forests if f['name'] == sel_forest)
            q_vol = q_vol.eq("forest_id", fid)
        vol_data = q_vol.execute().data
        df_vol = pd.DataFrame(vol_data)

        q_cost = backend.supabase.table("fact_operational_costs").select("*").eq("record_type", "Actual")
        if sel_forest != "ALL":
            if 'fid' in locals(): q_cost = q_cost.eq("forest_id", fid)
            else: 
                fid = next(f['id'] for f in forests if f['name'] == sel_forest)
                q_cost = q_cost.eq("forest_id", fid)

        cost_data = q_cost.execute().data
        df_cost = pd.DataFrame(cost_data)

        rev = 0; cost = 0
        if not df_vol.empty:
            df_vol['month'] = pd.to_datetime(df_vol['month'])
            df_vol = df_vol[df_vol['month'].dt.year == sel_year]
            rev = df_vol['amount'].sum()
        
        if not df_cost.empty:
            df_cost['month'] = pd.to_datetime(df_cost['month'])
            df_cost = df_cost[df_cost['month'].dt.year == sel_year]
            cost = df_cost['total_amount'].sum()
            
        margin = rev - cost

        k1, k2, k3 = st.columns(3)
        k1.metric("Total Revenue (Est)", f"${rev:,.0f}")
        k2.metric("Total Costs", f"${cost:,.0f}")
        k3.metric("Net Profit", f"${margin:,.0f}", delta=f"{(margin/rev*100) if rev else 0:.1f}%")

        st.divider()
        st.info("💡 提示：更详细的净额结算和发票生成，请前往 'Analysis & Invoice' 页面。")

    except Exception as e:
        st.error(f"Dashboard Error: {e}")

# --- 2. Analysis & Invoice (全面升级版) ---
def view_analysis_invoice():
    st.title("📈 Analysis & Invoicing (F360 Style)")
    
    forests = backend.get_forest_list()
    if not forests: return
    
    # --- A. 筛选栏 ---
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1: sel_forest = st.selectbox("Forest", [f['name'] for f in forests], key="inv_f")
    with c2: year = st.selectbox("Year", [2025, 2026], key="inv_y")
    with c3: month_str = st.selectbox("Month", MONTHS, key="inv_m")
    
    fid = next(f['id'] for f in forests if f['name'] == sel_forest)
    target_date = f"{year}-{MONTH_MAP[month_str]:02d}-01"
    
    # --- B. 数据获取 (Fine Granularity) ---
    with st.spinner("Fetching Transactional Data & GL Mappings..."):
        # 1. 获取 GL Mappings (需要 backend.py 支持 get_gl_mapping)
        # 如果 backend 还没更新，这里做一个容错
        try:
            cost_map, rev_map = backend.get_gl_mapping(fid)
        except AttributeError:
            st.warning("⚠️ backend.py 未包含 get_gl_mapping 函数，GL 功能将不可用。")
            cost_map, rev_map = {}, {}

        # 2. 获取销售数据 (Log Sales Transactions)
        # 注意：这里我们按月筛选，假设 transactions 里的 date 需要转换
        # 简单起见，这里拉取当月的数据
        start_date = target_date
        # 计算月末 (简单处理)
        if MONTH_MAP[month_str] == 12: end_date = f"{year+1}-01-01"
        else: end_date = f"{year}-{MONTH_MAP[month_str]+1:02d}-01"

        sales_res = backend.supabase.table("actual_sales_transactions")\
            .select("*, dim_products(grade_code)")\
            .eq("forest_id", fid).gte("date", start_date).lt("date", end_date).execute()
        df_sales = pd.DataFrame(sales_res.data)

        # 3. 获取成本数据 (Actual Costs)
        cost_res = backend.supabase.table("fact_operational_costs")\
            .select("*, dim_cost_activities(activity_name)")\
            .eq("forest_id", fid).eq("month", target_date).eq("record_type", "Actual").execute()
        df_costs = pd.DataFrame(cost_res.data)
        
        # 数据预处理：展平 Activity Name 和 Grade Code
        if not df_costs.empty:
            df_costs['activity'] = df_costs['dim_cost_activities'].apply(lambda x: x['activity_name'] if x else 'Unknown')
            # 应用 GL Mapping
            def apply_gl_cost(row):
                act_id = row['activity_id']
                mapping = cost_map.get(act_id) # 假设 map key 是 int
                if mapping: return mapping['code'], mapping['name']
                return "UNMAPPED", row['activity']
            
            df_costs[['gl_code', 'gl_desc']] = df_costs.apply(lambda row: pd.Series(apply_gl_cost(row)), axis=1)

        if not df_sales.empty:
            df_sales['grade'] = df_sales['dim_products'].apply(lambda x: x['grade_code'] if x else 'Unknown')
            # 应用 GL Mapping (Revenue)
            def apply_gl_rev(row):
                gid = row['grade_id']
                mapping = rev_map.get(gid)
                if mapping: return mapping['code'], mapping['name']
                return "UNMAPPED", f"Log Sales - {row['grade']}"
            
            df_sales[['gl_code', 'gl_desc']] = df_sales.apply(lambda row: pd.Series(apply_gl_rev(row)), axis=1)

    # --- C. 界面显示 ---
    
    tab_overview, tab_invoice, tab_finance = st.tabs(["📊 Budget Analysis", "📑 Statement Preview", "💳 Finance Export"])
    
    # [Tab 1: Budget Analysis] (保留原有逻辑，做简单对比)
    with tab_overview:
        # 这里为了简单，只用 Cost 对比
        bud_costs = backend.supabase.table("fact_operational_costs").select("total_amount").eq("forest_id", fid).eq("month", target_date).eq("record_type", "Budget").execute().data
        total_act = df_costs['total_amount'].sum() if not df_costs.empty else 0
        total_bud = sum([x['total_amount'] for x in bud_costs]) if bud_costs else 0
        
        c1, c2 = st.columns(2)
        c1.metric("Actual Costs", f"${total_act:,.0f}", delta=f"${total_bud - total_act:,.0f} (vs Budget)", delta_color="inverse")
        
        if not df_costs.empty:
            fig = px.bar(df_costs, x='activity', y='total_amount', title="Cost Breakdown by Activity")
            st.plotly_chart(fig, use_container_width=True)

    # [Tab 2: Statement Preview (F360 Style)]
    with tab_invoice:
        st.subheader("Invoice / Credit Note Generator")
        
        col_set, col_view = st.columns([1, 3])
        with col_set:
            st.markdown("### Settings")
            bill_to = st.text_input("Bill To", "CFG Forestry Group")
            mgmt_fee_pct = st.number_input("Mgmt Fee %", 0.0, 20.0, 8.0, 0.5)
            invoice_no = st.text_input("Ref No.", f"INV-{year}{MONTH_MAP[month_str]:02d}-{fid}")
            
            # 计算核心数据
            ctx = calculate_invoice_context(df_sales, df_costs, mgmt_fee_pct)
            
            st.divider()
            if ctx['total_due'] > 0:
                st.error(f"PAYABLE BY CFGC\n\n${ctx['total_due']:,.2f}")
            else:
                st.success(f"CREDIT TO CFGC\n\n${abs(ctx['total_due']):,.2f}")

        with col_view:
            st.markdown(f"### **TAX INVOICE / CREDIT NOTE**")
            st.markdown(f"**Date:** {date.today()} | **Ref:** {invoice_no}")
            
            # 第一部分：Debits (Costs)
            st.markdown("#### 1. Costs Incurred (Debits)")
            if not df_costs.empty:
                # 按 GL Code 或 Activity 汇总显示
                cost_view = df_costs.groupby(['activity', 'gl_code'])['total_amount'].sum().reset_index()
                st.dataframe(
                    cost_view, 
                    column_config={
                        "total_amount": st.column_config.NumberColumn("Amount", format="$%.2f"),
                        "gl_code": "GL Code"
                    },
                    hide_index=True, use_container_width=True
                )
            
            # Mgmt Fee
            st.markdown(f"**Management Fee ({mgmt_fee_pct}%):** `${ctx['mgmt_fee']:,.2f}`")
            st.markdown(f"**Total Debits:** `${ctx['costs'] + ctx['mgmt_fee']:,.2f}`")
            
            st.divider()
            
            # 第二部分：Credits (Revenue)
            st.markdown("#### 2. Revenue Credits (F360 Sales)")
            if not df_sales.empty:
                # 筛选出 Purchase 类型的销售
                if 'sale_type' in df_sales.columns:
                    credits_df = df_sales[df_sales['sale_type'].str.contains("Purchase", na=False, case=False)]
                else:
                    credits_df = df_sales
                
                if not credits_df.empty:
                    rev_view = credits_df.groupby(['grade', 'gl_code'])['total_value'].sum().reset_index()
                    st.dataframe(
                        rev_view,
                        column_config={
                            "total_value": st.column_config.NumberColumn("Credit", format="$%.2f"),
                            "gl_code": "GL Code"
                        },
                        hide_index=True, use_container_width=True
                    )
                else:
                    st.info("No 'Purchase' type sales found (Direct Sales only).")
            
            st.markdown(f"**Total Credits:** `-${ctx['revenue']:,.2f}`")
            
            st.divider()
            # 总结
            st.metric("NET TOTAL (Ex GST)", f"${ctx['subtotal_ex_gst']:,.2f}")

    # [Tab 3: Finance Export (Killer Feature)]
    with tab_finance:
        st.subheader("💳 CFG Finance Integration")
        st.markdown("Use this file to import directly into Xero/SAP.")
        
        # 构造财务报表：将 Cost 和 Revenue 合并
        finance_rows = []
        
        # 1. Costs
        if not df_costs.empty:
            grouped_costs = df_costs.groupby(['gl_code', 'gl_desc'])['total_amount'].sum().reset_index()
            for _, row in grouped_costs.iterrows():
                finance_rows.append({
                    "Type": "Debit (Cost)",
                    "GL Account": row['gl_code'],
                    "Account Name": row['gl_desc'],
                    "Amount": row['total_amount'],
                    "Reference": invoice_no
                })
        
        # 2. Mgmt Fee (通常也有一个固定的 GL Code)
        finance_rows.append({
            "Type": "Debit (Fee)",
            "GL Account": "6000-MGMT", # 示例代码
            "Account Name": "Management Fees",
            "Amount": ctx['mgmt_fee'],
            "Reference": invoice_no
        })
        
        # 3. Revenue
        if not df_sales.empty:
            # 同样的筛选逻辑
            if 'sale_type' in df_sales.columns:
                sales_export = df_sales[df_sales['sale_type'].str.contains("Purchase", na=False, case=False)]
            else:
                sales_export = df_sales
                
            grouped_rev = sales_export.groupby(['gl_code', 'gl_desc'])['total_value'].sum().reset_index()
            for _, row in grouped_rev.iterrows():
                finance_rows.append({
                    "Type": "Credit (Rev)",
                    "GL Account": row['gl_code'],
                    "Account Name": row['gl_desc'],
                    "Amount": -row['total_value'], # 负数表示 Credit
                    "Reference": invoice_no
                })
        
        df_fin = pd.DataFrame(finance_rows)
        
        st.dataframe(
            df_fin,
            column_config={"Amount": st.column_config.NumberColumn(format="$%.2f")},
            use_container_width=True, hide_index=True
        )
        
        if not df_fin.empty:
            csv = df_fin.to_csv(index=False).encode('utf-8')
            st.download_button(
                "⬇️ Download CSV for Finance",
                csv,
                f"AP_Import_{invoice_no}.csv",
                "text/csv",
                type="primary"
            )