import streamlit as st
import xmlrpc.client
import pandas as pd
from datetime import datetime, date, time
import time as time_lib
import json
import os
import ssl
import streamlit.components.v1 as components

# ===================================================================
# 1. CẤU HÌNH TRANG CHỦ STREAMLIT & CHỐNG TỰ ĐỘNG DỊCH TRANG
# ===================================================================
st.set_page_config(
    page_title="Hệ thống Quản lý & Tra cứu Quang Trắc", 
    layout="wide",
    page_icon="🔬"
)

IGNORED_JOBS_FILE = "ignored_jobs.json"

def load_ignored_jobs():
    if os.path.exists(IGNORED_JOBS_FILE):
        try:
            with open(IGNORED_JOBS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_ignored_job(job_id):
    ignored = load_ignored_jobs()
    if str(job_id) not in ignored:
        ignored.append(str(job_id))
        with open(IGNORED_JOBS_FILE, "w", encoding="utf-8") as f:
            json.dump(ignored, f, ensure_ascii=False, indent=2)

components.html(
    """
    <script>
        const mainDoc = window.parent.document;
        mainDoc.documentElement.classList.add('notranslate');
        mainDoc.body.classList.add('notranslate');
        
        if (!mainDoc.querySelector('meta[name="google"][content="notranslate"]')) {
            const meta = mainDoc.createElement('meta');
            meta.name = 'google';
            meta.content = 'notranslate';
            mainDoc.head.appendChild(meta);
        }
    </script>
    """,
    height=0,
    width=0
)

# -------------------------------------------------------------------
# CẤU HÌNH CHUNG KẾT NỐI ODOO
# -------------------------------------------------------------------
ODOO_URL = "https://erp.quatest3.com.vn"
ODOO_DB = "QUATEST3_18"
ODOO_USER = "thuoc.hh@quatest3.com.vn"
ODOO_PASSWORD = "294bca66617ec3498ac0530aea4d9cb72056a32d"

FIELDS_MAP = {
    'start_date': 'Ngày phân công',
    'deadline': 'Thời hạn',
    'manual_code': 'Mã quang trắc',
    'work_id': 'Công việc',               
    'testing_method_id': 'Testing method', 
    'qt_uom_id': 'ĐVT',
    'note': 'Ghi chú',
    'result_testing': 'Kết quả',           
    'assignee_ids': 'Người được phân công'
}

LOOKUP_SAMPLE_FIELDS = ['parent_id', 'sample_name', 'sample_id', 'sample', 'product_id', 'sample_description']

STATE_MAP = {
    'draft': 'Dự thảo',
    'assigned': 'Đã phân công',
    'in_progress': 'Đang thực hiện',
    'import_result': 'Đang nhập kết quả',
    'done': 'Hoàn thành',
    'cancel': 'Đã hủy'
}

def get_odoo_user_and_partner_ids():
    try:
        ctx = ssl._create_unverified_context()
        common = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/common', allow_none=True, context=ctx)
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
        models = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/object', allow_none=True, context=ctx)

        user_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'res.users', 'search', [[['name', 'ilike', 'N Văn Phương']]])
        partner_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'res.partner', 'search', [[['name', 'ilike', 'N Văn Phương']]])
        return set(user_ids + partner_ids)
    except Exception:
        return set()

def get_assign_work_line_fields():
    try:
        ctx = ssl._create_unverified_context()
        common = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/common', allow_none=True, context=ctx)
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
        models = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/object', allow_none=True, context=ctx)

        existing_fields_info = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'assign.work.line', 'fields_get', [], {'attributes': ['string']}
        )
        return set(existing_fields_info.keys())
    except Exception:
        return set()

def clean_odoo_field_value(x):
    if not x:
        return ''
    if isinstance(x, (list, tuple)) and len(x) == 2 and isinstance(x[0], int) and isinstance(x[1], str):
        return x[1]
    if isinstance(x, (list, tuple)):
        names = []
        for item in x:
            if isinstance(item, (list, tuple)) and len(item) > 1:
                names.append(str(item[1]))
            elif isinstance(item, str):
                names.append(item)
        if names:
            return ", ".join(names)
    return str(x)

def extract_sample_value(row, active_sample_fields):
    for sf in active_sample_fields:
        val = row.get(sf)
        cleaned = clean_odoo_field_value(val).strip()
        if cleaned and cleaned.lower() != 'false':
            return cleaned
    return ''


# ===================================================================
# 2. CHỨC NĂNG 1: TRA CỨU MÃ QUANG TRẮC
# ===================================================================
def fetch_lookup_data_from_odoo(query_str):
    if not query_str.strip():
        return pd.DataFrame()
    
    max_retries = 3
    valid_ids = get_odoo_user_and_partner_ids()
    valid_odoo_fields = get_assign_work_line_fields()

    for attempt in range(max_retries):
        try:
            ctx = ssl._create_unverified_context()
            common = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/common', allow_none=True, context=ctx)
            uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
            models = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/object', allow_none=True, context=ctx)

            actual_fields_map = {}
            for k, v in FIELDS_MAP.items():
                if k in valid_odoo_fields:
                    actual_fields_map[k] = v
                elif k == 'work_id' and 'name' in valid_odoo_fields:
                    actual_fields_map['name'] = v
                elif k == 'testing_method_id' and 'test_method_id' in valid_odoo_fields:
                    actual_fields_map['test_method_id'] = v

            active_sample_fields = [sf for sf in LOOKUP_SAMPLE_FIELDS if sf in valid_odoo_fields]
            for sf in active_sample_fields:
                actual_fields_map[sf] = sf

            if 'state' in valid_odoo_fields:
                actual_fields_map['state'] = 'Trạng thái'

            code_field_list = [k for k, v in actual_fields_map.items() if v == 'Mã quang trắc']
            if not code_field_list:
                return pd.DataFrame()

            search_field = code_field_list[0]
            search_term = query_str.strip()

            domain = [[search_field, 'ilike', search_term]]
            fields_to_read = list(actual_fields_map.keys())

            all_records = []
            offset = 0
            batch_size = 500
            
            while True:
                records = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD,
                    'assign.work.line', 'search_read',
                    [domain], {
                        'fields': fields_to_read, 
                        'offset': offset, 
                        'limit': batch_size,
                        'order': 'id asc'
                    }
                )
                if not records:
                    break
                all_records.extend(records)
                if len(records) < batch_size:
                    break
                offset += batch_size

            if not all_records:
                return pd.DataFrame()

            df = pd.DataFrame(all_records)

            if 'assignee_ids' in df.columns:
                def is_phuong(val):
                    if valid_ids and isinstance(val, list):
                        for item in val:
                            if isinstance(item, int) and item in valid_ids:
                                return True
                            if isinstance(item, (list, tuple)) and item[0] in valid_ids:
                                return True
                    name_str = clean_odoo_field_value(val).lower()
                    return 'n văn phương' in name_str or 'văn phương' in name_str

                df = df[df['assignee_ids'].apply(is_phuong)].copy()

            if df.empty:
                return pd.DataFrame()

            df['Mẫu'] = df.apply(lambda r: extract_sample_value(r, active_sample_fields), axis=1)

            for col in df.columns:
                if col != 'Mẫu':
                    df[col] = df[col].apply(clean_odoo_field_value)

            state_col = [k for k, v in actual_fields_map.items() if v == 'Trạng thái']
            if state_col and state_col[0] in df.columns:
                df[state_col[0]] = df[state_col[0]].map(STATE_MAP).fillna(df[state_col[0]])

            date_cols = [k for k, v in actual_fields_map.items() if v in ['Ngày phân công', 'Thời hạn']]
            for d_col in date_cols:
                if d_col in df.columns:
                    df[d_col] = pd.to_datetime(df[d_col], errors='coerce').dt.strftime('%d/%m/%Y').fillna('')

            df = df.rename(columns=actual_fields_map)

            sort_cols = [c for c in ['Mã quang trắc', 'Công việc'] if c in df.columns]
            if sort_cols:
                df = df.sort_values(by=sort_cols, key=lambda col: col.astype(str)).reset_index(drop=True)

            return df
        except Exception:
            if attempt < max_retries - 1:
                time_lib.sleep(1)
                continue
            else:
                return pd.DataFrame()

def render_lookup_html_table(df):
    if df.empty:
        return ""

    cols_order = ['Ngày phân công', 'Thời hạn', 'Mẫu', 'Mã quang trắc', 'Công việc', 'Testing method', 'ĐVT', 'Ghi chú', 'Kết quả', 'Trạng thái']
    existing_cols = [c for c in cols_order if c in df.columns]
    df = df[existing_cols].fillna('')

    if 'Mã quang trắc' in df.columns:
        df['_base_code'] = df['Mã quang trắc'].apply(lambda x: str(x).split('.')[0] if x else '')
    else:
        df['_base_code'] = ''

    col_widths = {
        'Ngày phân công': '100px',
        'Thời hạn': '100px',
        'Mẫu': '180px',
        'Mã quang trắc': '160px',
        'Công việc': '230px',
        'Testing method': '200px',
        'ĐVT': '85px',
        'Ghi chú': '110px',
        'Kết quả': '100px',
        'Trạng thái': '110px'
    }

    html_lines = []
    html_lines.append('<style>')
    html_lines.append('  .custom-table-lookup { width: 100%; border-collapse: collapse; margin-top: 15px; font-family: sans-serif; font-size: 14px; table-layout: fixed; }')
    html_lines.append('  .custom-table-lookup th, .custom-table-lookup td { border: 1px solid #D3D3D3; padding: 8px 12px; text-align: left; vertical-align: middle; word-wrap: break-word; overflow-wrap: break-word; }')
    html_lines.append('  .custom-table-lookup th { background-color: #F0F2F6; color: #1F2937; font-weight: bold; }')
    html_lines.append('  .custom-table-lookup tr:nth-child(even) { background-color: #FAFAFA; }')
    html_lines.append('  .code-group-border { border-bottom: 3px solid #000000 !important; }')
    html_lines.append('</style>')

    html_lines.append('<table class="custom-table-lookup"><thead><tr>')
    for col in existing_cols:
        width_style = f' style="width: {col_widths.get(col, "auto")};"'
        html_lines.append(f'<th{width_style}>{col}</th>')
    html_lines.append('</tr></thead><tbody>')

    main_merge_cols = [c for c in ['Ngày phân công', 'Thời hạn'] if c in existing_cols]
    code_merge_cols = [c for c in ['Mẫu', 'Mã quang trắc'] if c in existing_cols]

    n = len(df)
    i = 0
    while i < n:
        j = i + 1
        while j < n and df.iloc[j]['_base_code'] == df.iloc[i]['_base_code']:
            j += 1
        base_rowspan = j - i

        sub_i = i
        while sub_i < j:
            sub_j = sub_i + 1
            while sub_j < j and df.iloc[sub_j]['Mã quang trắc'] == df.iloc[sub_i]['Mã quang trắc']:
                sub_j += 1
            code_rowspan = sub_j - sub_i

            for r in range(sub_i, sub_j):
                html_lines.append('<tr>')
                is_last_row_of_code = (r == sub_j - 1)
                border_class = ' class="code-group-border"' if is_last_row_of_code else ''

                for col in existing_cols:
                    val = str(df.iloc[r][col])

                    if col in main_merge_cols:
                        if r == i:
                            html_lines.append(f'<td rowspan="{base_rowspan}">{val}</td>')
                    elif col in code_merge_cols:
                        if r == sub_i:
                            html_lines.append(f'<td rowspan="{code_rowspan}"{border_class}>{val}</td>')
                    else:
                        html_lines.append(f'<td{border_class}>{val}</td>')
                html_lines.append('</tr>')

            sub_i = sub_j
        i = j

    html_lines.append('</tbody></table>')
    return "".join(html_lines)

def run_lookup_app():
    st.subheader("🔍 Tra cứu thông tin Quang Trắc")
    search_query = st.text_input("Nhập số mã quang cần tìm (nhấn Enter để tìm kiếm):")

    if search_query.strip():
        with st.spinner("Đang tìm kiếm..."):
            df_result = fetch_lookup_data_from_odoo(search_query)
        
        if not df_result.empty:
            html_table = render_lookup_html_table(df_result)
            st.markdown(
                f'<div style="overflow-x: auto; width: 100%;" translate="no">{html_table}</div>', 
                unsafe_allow_html=True
            )
        else:
            st.info("Không tìm thấy dữ liệu phù hợp.")


# ===================================================================
# 3. CHỨC NĂNG 2: QUẢN LÝ (TẢI 1 LẦN VÀO RAM - LỌC TỨC THÌ)
# ===================================================================
def fetch_management_data_from_odoo():
    """Hàm duy nhất kết nối Odoo để kéo dữ liệu"""
    max_retries = 3
    valid_ids = get_odoo_user_and_partner_ids()
    valid_odoo_fields = get_assign_work_line_fields()

    if not valid_ids or not valid_odoo_fields:
        return pd.DataFrame()

    for attempt in range(max_retries):
        try:
            ctx = ssl._create_unverified_context()
            common = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/common', allow_none=True, context=ctx)
            uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
            models = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/object', allow_none=True, context=ctx)

            actual_fields_map = {}
            for k, v in FIELDS_MAP.items():
                if k in valid_odoo_fields:
                    actual_fields_map[k] = v
                elif k == 'work_id' and 'name' in valid_odoo_fields:
                    actual_fields_map['name'] = v
                elif k == 'testing_method_id' and 'test_method_id' in valid_odoo_fields:
                    actual_fields_map['test_method_id'] = v

            active_sample_fields = [sf for sf in LOOKUP_SAMPLE_FIELDS if sf in valid_odoo_fields]
            for sf in active_sample_fields:
                actual_fields_map[sf] = sf

            bold_fields = ['is_bold', 'is_header', 'display_type', 'is_title']
            active_bold_fields = [f for f in bold_fields if f in valid_odoo_fields]
            for f in active_bold_fields:
                actual_fields_map[f] = f

            state_work_field = None
            for sf in ['state', 'work_state', 'status', 'stage_id']:
                if sf in valid_odoo_fields:
                    state_work_field = sf
                    actual_fields_map[sf] = 'Trạng thái công việc'
                    break

            order_rel_field = None
            for rel in ['order_id', 'sale_order_id', 'sale_id']:
                if rel in valid_odoo_fields:
                    order_rel_field = rel
                    actual_fields_map[rel] = 'order_id_raw'
                    break

            res_field = 'result_testing' if 'result_testing' in valid_odoo_fields else 'result'
            code_field = 'manual_code' if 'manual_code' in valid_odoo_fields else 'code'

            domain = [
                (res_field, '=', False),
                ('assignee_ids', 'in', list(valid_ids)),
                (code_field, '!=', False)
            ]
            if 'start_date' in valid_odoo_fields:
                domain.append(('start_date', '>=', '2026-09-01'))

            fields_to_read = list(actual_fields_map.keys())

            record_ids = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'assign.work.line', 'search',
                [domain], {'order': 'id asc'}
            )

            if not record_ids:
                return pd.DataFrame()

            records = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'assign.work.line', 'read',
                [record_ids],
                {'fields': fields_to_read}
            )

            if not records:
                return pd.DataFrame()

            all_assignee_user_ids = set()
            for r in records:
                a_ids = r.get('assignee_ids')
                if isinstance(a_ids, list):
                    all_assignee_user_ids.update(a_ids)

            user_name_map = {}
            if all_assignee_user_ids:
                users_data = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD,
                    'res.users', 'read',
                    [list(all_assignee_user_ids)],
                    {'fields': ['id', 'name']}
                )
                user_name_map = {u['id']: u['name'] for u in users_data}

            for r in records:
                a_ids = r.get('assignee_ids')
                if isinstance(a_ids, list):
                    r['assignee_ids'] = ", ".join([user_name_map.get(uid_item, str(uid_item)) for uid_item in a_ids if uid_item in user_name_map])

            df = pd.DataFrame(records)

            for f in active_bold_fields:
                if f in df.columns:
                    if f == 'display_type':
                        df = df[~df[f].isin(['line_section', 'line_note'])].copy()
                    else:
                        df = df[df[f] != True].copy()

            if state_work_field and state_work_field in df.columns:
                def is_valid_work_stage(val):
                    val_str = clean_odoo_field_value(val).lower()
                    valid_keywords = ['đang làm', 'nhập kq', 'trưởng nhóm', 'draft', 'doing', 'in_progress', 'leader', 'wait_leader', 'wait_group_leader']
                    invalid_keywords = ['tđv', 'hoàn tất', 'done', 'approved', 'completed', 'finish']

                    if any(inv in val_str for inv in invalid_keywords):
                        return False
                    return any(v in val_str for v in valid_keywords)

                df = df[df[state_work_field].apply(is_valid_work_stage)].copy()

            if df.empty:
                return pd.DataFrame()

            if order_rel_field and order_rel_field in df.columns:
                sale_order_ids = []
                for val in df[order_rel_field].dropna():
                    if isinstance(val, (list, tuple)) and len(val) > 0 and isinstance(val[0], int):
                        sale_order_ids.append(val[0])
                    elif isinstance(val, int):
                        sale_order_ids.append(val)

                sale_order_ids = list(set(sale_order_ids))

                if sale_order_ids:
                    orders_data = models.execute_kw(
                        ODOO_DB, uid, ODOO_PASSWORD,
                        'sale.order', 'read',
                        [sale_order_ids],
                        {'fields': ['id', 'state_department', 'state']}
                    )
                    
                    valid_order_ids = set()
                    for o in orders_data:
                        st_dept = str(o.get('state_department', '')).lower()
                        if st_dept == 'in_progress' and 'waiting' not in st_dept and 'sent' not in st_dept:
                            valid_order_ids.add(o['id'])

                    def is_order_valid(val):
                        if isinstance(val, (list, tuple)) and len(val) > 0:
                            return val[0] in valid_order_ids
                        elif isinstance(val, int):
                            return val in valid_order_ids
                        return False

                    df = df[df[order_rel_field].apply(is_order_valid)].copy()

            if df.empty:
                return pd.DataFrame()

            deadline_col = [k for k, v in actual_fields_map.items() if v == 'Thời hạn'][0]
            start_date_col = [k for k, v in actual_fields_map.items() if v == 'Ngày phân công'][0]

            df['deadline_dt'] = pd.to_datetime(df[deadline_col], errors='coerce')
            df['start_date_dt'] = pd.to_datetime(
