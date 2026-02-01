import streamlit as st
import pandas as pd
import backend
import time

def view_admin_upload():
    st.title("⚙️ Admin: Chart of Accounts Setup")
    st.markdown("### 上传会计科目映射表 (GL Mapping)")
    # [修改点 1] 提示文字更新为 Company
    st.info("请上传包含以下列的 Excel/CSV: `Company`, `Type` (Cost/Revenue), `Item Name`, `GL Code`, `GL Name`")

    uploaded_file = st.file_uploader("Upload Mapping File", type=['csv', 'xlsx'])
    
    if uploaded_file and st.button("🚀 Process & Upload", type="primary"):
        try:
            # 1. 读取文件
            if uploaded_file.name.endswith('.csv'):
                df = pd.read_csv(uploaded_file)
            else:
                df = pd.read_excel(uploaded_file)
            
            # [修改点 2] 检查关键列名是否存在
            if 'Company' not in df.columns and 'Forest' in df.columns:
                st.warning("⚠️ 提示：检测到表头是 'Forest'，建议下次改为 'Company'。本次将自动按 'Company' 处理。")
                df.rename(columns={'Forest': 'Company'}, inplace=True)
            
            if 'Company' not in df.columns:
                st.error("❌ 错误：文件中缺少 `Company` 列！请检查表头。")
                return

            st.write("👀 文件预览 (前5行):", df.head())
            
            # 2. 获取系统基础数据
            with st.spinner("正在同步数据库基础信息..."):
                # 注意：数据库里表名可能还是 dim_forests，但里面存的是公司实体名(CFGCNZ等)
                forests = backend.supabase.table("dim_forests").select("*").execute().data
                activities = backend.supabase.table("dim_cost_activities").select("*").execute().data
                products = backend.supabase.table("dim_products").select("*").execute().data
            
            forest_map = {f['name']: f['id'] for f in forests}
            act_map = {a['activity_name']: a['id'] for a in activities}
            prod_map = {p['grade_code']: p['id'] for p in products} 
            
            records = []
            errors = []
            
            # 3. 循环处理
            progress_bar = st.progress(0)
            for i, row in df.iterrows():
                try:
                    # [修改点 3] 读取 Company 列来查找 ID
                    company_name = row.get('Company')
                    fid = forest_map.get(company_name)
                    
                    if not fid:
                        errors.append(f"Row {i+1}: Company '{company_name}' 未在系统中找到 (请检查 dim_forests 配置)")
                        continue
                    
                    # B. 找 Item ID
                    item_type = row['Type']
                    item_name = row['Item Name']
                    item_id = None
                    
                    if item_type == 'Cost':
                        item_id = act_map.get(item_name)
                        if not item_id: # 模糊匹配
                            for k, v in act_map.items():
                                if k in str(item_name) or str(item_name) in k:
                                    item_id = v; break
                    elif item_type == 'Revenue':
                        item_id = prod_map.get(item_name)
                    
                    if not item_id:
                        errors.append(f"Row {i+1}: Item '{item_name}' ({item_type}) 系统里没有这个项目")
                        continue
                    
                    # C. 构建记录
                    records.append({
                        "forest_id": fid, # 数据库字段仍叫 forest_id，但逻辑上存的是 Company ID
                        "item_type": item_type,
                        "item_id": item_id,
                        "gl_code": str(row['GL Code']),
                        "gl_name": row['GL Name']
                    })
                    
                except Exception as e:
                    errors.append(f"Row {i+1}: 数据格式错误 {str(e)}")
                
                progress_bar.progress((i+1)/len(df))
                
            # 4. 写入数据库
            if records:
                try:
                    backend.supabase.table("dim_gl_mappings").upsert(records, on_conflict="forest_id,item_type,item_id").execute()
                    st.success(f"✅ 成功导入 {len(records)} 条会计科目映射！")
                    time.sleep(1)
                except Exception as e:
                    st.error(f"数据库写入失败: {e}")
            
            if errors:
                st.warning(f"⚠️ 有 {len(errors)} 行数据处理失败:")
                st.dataframe(pd.DataFrame(errors, columns=["Error Log"]), use_container_width=True)

        except Exception as e:
            st.error(f"文件处理失败: {e}")