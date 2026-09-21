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

# -------------------------------------------------------------------
# LƯU TRỮ DỮ LIỆU HẸN LẠI RIÊNG TẠI MÁY (RESCHEDULE STORE)
# -------------------------------------------------------------------
RESCHEDULE_FILE = "reschedule_data.json"

def load_reschedule_data():
    if os.path.exists(RESCHEDULE_FILE):
        try:
            with open(RESCHEDULE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"codes": {}, "jobs": {}}
    return {"codes": {}, "jobs": {}}

def save_reschedule_data(data):
    with open(RESCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_reschedule_code(code, new_deadline_str, original_deadline_str):
    data = load_reschedule_data()
    data["codes"][code] = {
        "original_deadline": original_deadline_str,
        "rescheduled_deadline": new_deadline_str,
        "ack": False
    }
    save_reschedule_data(data)

def add_reschedule_job(job_key, new_deadline_str, original_deadline_str):
    data = load_reschedule_data()
    data["jobs"][job_key] = {
        "original_deadline": original_deadline_str,
        "rescheduled_deadline": new_deadline_str,
        "ack": False
    }
    save_reschedule_data(data)

def ack_reschedule(res_type, key):
    data = load_reschedule_data()
    if res_type == "code" and key in data.get("codes", {}):
        data["codes"][key]["ack"] = True
        save_reschedule_data(data)
    elif res_type == "code_group":
        for k in key.split(","):
            if k in data.get("codes", {}):
                data["codes"][k]["ack"] = True
        save_reschedule_data(data)
    elif res_type == "job" and key in data.get("jobs", {}):
        data["jobs"][key]["ack"] = True
        save_reschedule_data(data)

# Chống Google Translate làm thay đổi cấu trúc dữ liệu
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
# CẤU HÌNH CHUNG KẾT NỐI ODOO & CACHE THÔNG TIN HỆ THỐNG (OPTIMIZED)
# -------------------------------------------------------------------
ODOO_URL = "https://erp.quatest3.com.vn"
ODOO_DB = "QUATEST3_18"
ODOO_USER = "thuoc.hh@quatest3.com.vn"
ODOO_PASSWORD = "ce7bedfb5be533accb6a5126d2c1c4a85a298a84"

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

STATE_MAP = {
    'draft': 'Dự thảo',
    'assigned': 'Đã phân công',
    'in_progress': 'Đang thực hiện',
    'import_result': 'Đang nhập kết quả',
    'done': 'Hoàn thành',
    'cancel': 'Đã hủy'
}

@st.cache_data(ttl=3600, show_spinner=False)
def get_odoo_user_and_partner_ids():
    """Cache ID người dùng để tránh gọi Odoo tìm kiếm liên tục (Save network calls)"""
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

@st.cache_data(ttl=3600, show_spinner=False)
def get_assign_work_line_fields():
    """Cache cấu hình danh sách trường thông tin Odoo"""
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

def parse_reschedule_dt(dt_str):
    if not dt_str:
        return None
    dt_str = str(dt_str).strip()
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y"
    ]
    for fmt in formats:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            pass
    return None

def format_rescheduled_display(dt_str):
    if not dt_str:
        return ''
    dt = parse_reschedule_dt(dt_str)
    if dt:
        return dt.strftime("%d/%m/%Y %H:%M")
    return str(dt_str)

def clean_odoo_field_value(x):
    """Bóc tách lấy Tên hiển thị từ dữ liệu Odoo"""
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


# ===================================================================
# 2. CHỨC NĂNG 1: TRA CỨU MÃ QUANG TRẮC (TỐI ƯU VỚI CACHE)
# ===================================================================
@st.cache_data(ttl=300, show_spinner=False)
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

            for col in df.columns:
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

    cols_order = ['Ngày phân công', 'Thời hạn', 'Mã quang trắc', 'Công việc', 'Testing method', 'ĐVT', 'Ghi chú', 'Kết quả', 'Trạng thái']
    existing_cols = [c for c in cols_order if c in df.columns]
    df = df[existing_cols].fillna('')

    if 'Mã quang trắc' in df.columns:
        df['_base_code'] = df['Mã quang trắc'].apply(lambda x: str(x).split('.')[0] if x else '')
    else:
        df['_base_code'] = ''

    html_lines = []
    html_lines.append('<style>')
    html_lines.append('  .custom-table-lookup { width: 100%; border-collapse: collapse; margin-top: 15px; font-family: sans-serif; font-size: 14px; }')
    html_lines.append('  .custom-table-lookup th, .custom-table-lookup td { border: 1px solid #D3D3D3; padding: 8px 12px; text-align: left; vertical-align: middle; }')
    html_lines.append('  .custom-table-lookup th { background-color: #F0F2F6; color: #1F2937; font-weight: bold; }')
    html_lines.append('  .custom-table-lookup tr:nth-child(even) { background-color: #FAFAFA; }')
    html_lines.append('  .code-group-border { border-bottom: 3px solid #000000 !important; }')
    html_lines.append('</style>')

    html_lines.append('<table class="custom-table-lookup"><thead><tr>')
    for col in existing_cols:
        html_lines.append(f'<th>{col}</th>')
    html_lines.append('</tr></thead><tbody>')

    main_merge_cols = [c for c in ['Ngày phân công', 'Thời hạn'] if c in existing_cols]

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

                for col in existing_cols:
                    val = str(df.iloc[r][col])

                    if col in main_merge_cols:
                        if r == i:
                            html_lines.append(f'<td rowspan="{base_rowspan}">{val}</td>')
                    elif col == 'Mã quang trắc':
                        if r == sub_i:
                            html_lines.append(f'<td rowspan="{code_rowspan}" class="code-group-border">{val}</td>')
                    else:
                        border_class = ' class="code-group-border"' if is_last_row_of_code else ''
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
# 3. CHỨC NĂNG 2: QUẢN LÝ THỜI HẠN MÃ QUANG TRẮC (TỐI ƯU TỐC ĐỘ)
# ===================================================================
@st.cache_data(ttl=300, show_spinner=False)
def fetch_management_data_from_odoo():
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
            df['start_date_dt'] = pd.to_datetime(df[start_date_col], errors='coerce')

            for col in df.columns:
                if col not in ['deadline_dt', 'start_date_dt', 'Trạng thái hạn', 'assignee_ids']:
                    df[col] = df[col].apply(clean_odoo_field_value)

            df = df.rename(columns=actual_fields_map)

            if 'Mã quang trắc' in df.columns:
                df = df[df['Mã quang trắc'].astype(str).str.strip().ne('')].copy()

            # LỌC DUMAS VÀ KJELDAHL
            if 'Mã quang trắc' in df.columns and 'Testing method' in df.columns:
                method_series = df['Testing method'].astype(str).str.lower()
                kd_df = df[method_series.str.contains('kjeldahl|dumas', regex=True, na=False)]
                
                if not kd_df.empty:
                    candidate_codes = kd_df['Mã quang trắc'].astype(str).str.strip().unique().tolist()
                    if candidate_codes:
                        try:
                            method_field_in_odoo = 'testing_method_id' if 'testing_method_id' in valid_odoo_fields else ('test_method_id' if 'test_method_id' in valid_odoo_fields else False)
                            
                            if method_field_in_odoo:
                                check_domain = [
                                    (code_field, 'in', candidate_codes),
                                    (res_field, '!=', False)
                                ]
                                completed_records = models.execute_kw(
                                    ODOO_DB, uid, ODOO_PASSWORD,
                                    'assign.work.line', 'search_read',
                                    [check_domain],
                                    {'fields': [code_field, method_field_in_odoo, res_field]}
                                )
                                
                                codes_with_completed_dumas = set()
                                codes_with_completed_kjeldahl = set()
                                
                                for cr in completed_records:
                                    c_code = clean_odoo_field_value(cr.get(code_field)).strip()
                                    c_method = clean_odoo_field_value(cr.get(method_field_in_odoo)).lower()
                                    c_res = clean_odoo_field_value(cr.get(res_field)).strip()
                                    
                                    if c_res != '' and c_res.lower() != 'false':
                                        if 'dumas' in c_method:
                                            codes_with_completed_dumas.add(c_code)
                                        if 'kjeldahl' in c_method:
                                            codes_with_completed_kjeldahl.add(c_code)
                                
                                def filter_kd_dumas(row):
                                    r_code = str(row.get('Mã quang trắc', '')).strip()
                                    r_method = str(row.get('Testing method', '')).lower()
                                    
                                    if 'kjeldahl' in r_method and r_code in codes_with_completed_dumas:
                                        return False
                                    if 'dumas' in r_method and r_code in codes_with_completed_kjeldahl:
                                        return False
                                    return True
                                
                                df = df[df.apply(filter_kd_dumas, axis=1)].copy()
                        except Exception:
                            pass

            if 'start_date_dt' in df.columns and 'Ngày phân công' in df.columns:
                df['Ngày phân công'] = df['start_date_dt'].dt.strftime('%d/%m/%Y').fillna('')
            if 'deadline_dt' in df.columns and 'Thời hạn' in df.columns:
                df['Thời hạn'] = df['deadline_dt'].dt.strftime('%d/%m/%Y').fillna('')

            return df

        except Exception as e:
            if attempt < max_retries - 1:
                time_lib.sleep(1)
                continue
            else:
                st.error(f"Lỗi kết nối hoặc truy vấn Odoo: {e}")
                return pd.DataFrame()

def render_management_html_table(df, reschedule_info):
    if df.empty:
        return ""

    cols_order = [
        'Trạng thái hạn', 'Ngày phân công', 'Thời hạn', 
        'Mã quang trắc', 'Công việc', 'Testing method', 'ĐVT', 'Ghi chú'
    ]
    existing_cols = [c for c in cols_order if c in df.columns]
    df_display = df[existing_cols].fillna('')

    if 'Mã quang trắc' in df_display.columns:
        df_display['_group_code'] = df_display['Mã quang trắc'].apply(lambda x: str(x).split('.')[0].strip() if x else '')
    else:
        df_display['_group_code'] = df_display.index

    html_lines = []
    html_lines.append('<style>')
    html_lines.append('  .custom-table-mgmt { width: 100%; border-collapse: collapse !important; margin-top: 15px; font-family: sans-serif; font-size: 14px; }')
    html_lines.append('  .custom-table-mgmt th { background-color: #E2E8F0; color: #1A202C; font-weight: bold; text-align: center; border: 1px solid #CBD5E0; padding: 10px 12px; }')
    html_lines.append('  .custom-table-mgmt td { border-left: 1px solid #CBD5E0; border-right: 1px solid #CBD5E0; border-top: 1px solid #CBD5E0; border-bottom: 1px solid #CBD5E0; padding: 8px 12px; text-align: left; vertical-align: top; }')
    html_lines.append('  .top-border-root { border-top: 2.5px solid #2D3748 !important; }')
    html_lines.append('  .top-border-sub { border-top: 1.5px solid #718096 !important; }')
    html_lines.append('  .bg-group-0 { background-color: #FFFFFF !important; }')
    html_lines.append('  .bg-group-1 { background-color: #EDF2F7 !important; }')
    
    html_lines.append('''
    @keyframes alert-shake-pulse {
        0% { transform: scale(1) rotate(0deg); }
        20% { transform: scale(1.35) rotate(-14deg); }
        40% { transform: scale(1.35) rotate(14deg); }
        60% { transform: scale(1.35) rotate(-8deg); }
        80% { transform: scale(1.35) rotate(8deg); }
        100% { transform: scale(1) rotate(0deg); }
    }
    .alert-triangle-animated {
        display: inline-block;
        font-size: 18px;
        animation: alert-shake-pulse 0.75s infinite;
        line-height: 1;
        margin-top: 2px;
        margin-left: 4px;
    }
    .speech-bubble-wrapper {
        position: relative;
        display: inline-block;
        cursor: pointer;
    }
    .speech-bubble-pop {
        visibility: hidden;
        opacity: 0;
        width: max-content;
        max-width: 240px;
        background-color: #FFFFFF;
        color: #10B981;
        border: 2px solid #10B981;
        text-align: center;
        border-radius: 12px;
        padding: 6px 12px;
        position: absolute;
        z-index: 9999;
        bottom: 125%;
        left: 50%;
        transform: translateX(-20%);
        transition: opacity 0.2s ease-in-out;
        font-size: 13px;
        font-weight: bold;
        box-shadow: 0px 4px 12px rgba(0, 0, 0, 0.15);
        pointer-events: none;
    }
    .speech-bubble-pop::after {
        content: "";
        position: absolute;
        top: 100%;
        left: 18px;
        border-width: 7px;
        border-style: solid;
        border-color: #10B981 transparent transparent transparent;
    }
    .speech-bubble-pop::before {
        content: "";
        position: absolute;
        top: 100%;
        left: 19px;
        border-width: 5px;
        border-style: solid;
        border-color: #FFFFFF transparent transparent transparent;
        z-index: 1;
    }
    .speech-bubble-wrapper:hover .speech-bubble-pop,
    .speech-bubble-wrapper:focus .speech-bubble-pop {
        visibility: visible;
        opacity: 1;
    }
    ''')
    html_lines.append('</style>')

    html_lines.append('<table class="custom-table-mgmt"><thead><tr>')
    for col in existing_cols:
        header_title = "" if col == 'Trạng thái hạn' else col
        html_lines.append(f'<th>{header_title}</th>')
    html_lines.append('</tr></thead><tbody>')

    n = len(df_display)
    i = 0
    group_idx = 0

    while i < n:
        j = i + 1
        root_code_i = df_display.iloc[i]['_group_code']
        while j < n and df_display.iloc[j]['_group_code'] == root_code_i:
            j += 1
        root_rowspan = j - i

        bg_class = f"bg-group-{group_idx % 2}"

        group_rows = df.iloc[i:j]
        group_sub_codes = set(group_rows['Mã quang trắc'].astype(str).str.strip().unique())
        
        active_res_sub_codes = set()
        first_code_res_info = None

        for r_idx in range(i, j):
            r_data = df.iloc[r_idx]
            c_res = r_data.get('_code_reschedule')
            if c_res and not c_res['ack']:
                active_res_sub_codes.add(str(r_data['Mã quang trắc']).strip())
                if not first_code_res_info:
                    first_code_res_info = c_res

        total_sub_codes = len(group_sub_codes)
        res_sub_codes_count = len(active_res_sub_codes)

        all_codes_rescheduled = (total_sub_codes > 0 and res_sub_codes_count == total_sub_codes)
        partial_codes_rescheduled = (0 < res_sub_codes_count < total_sub_codes)

        curr = i
        while curr < j:
            k = curr + 1
            exact_code_curr = str(df_display.iloc[curr]['Mã quang trắc']) if 'Mã quang trắc' in existing_cols else ''
            while k < j and str(df_display.iloc[k]['Mã quang trắc']) == exact_code_curr:
                k += 1
            sub_rowspan = k - curr

            for r in range(curr, k):
                html_lines.append('<tr>')
                
                row_raw = df.iloc[r]
                code_res = row_raw.get('_code_reschedule')
                job_res = row_raw.get('_job_reschedule')

                for col in existing_cols:
                    val = str(df_display.iloc[r][col])

                    if col == 'Trạng thái hạn':
                        if r == i:
                            align = ' style="text-align: center;"'
                            root_border = ' top-border-root' if i > 0 else ''
                            cell_content = val
                            
                            if all_codes_rescheduled and first_code_res_info:
                                res_d_fmt = format_rescheduled_display(first_code_res_info['rescheduled_deadline'])
                                is_due = any(df.iloc[rx].get('_code_reschedule', {}).get('is_due', False) for rx in range(i, j) if df.iloc[rx].get('_code_reschedule'))
                                
                                if is_due:
                                    tri_cls = "alert-triangle-animated"
                                    tri_html = f'<br/><span class="{tri_cls}" title="Đã đến hạn ({res_d_fmt})! Xác nhận ở bảng trên để ẩn">⚠️</span>'
                                    cell_content += tri_html
                                else:
                                    tri_html = f'<br/><div class="speech-bubble-wrapper" tabindex="0"><span style="font-size:16px;">⚠️</span><div class="speech-bubble-pop">Hẹn lại vào: {res_d_fmt}</div></div>'
                                    cell_content += tri_html

                            html_lines.append(f'<td rowspan="{root_rowspan}" class="{bg_class}{root_border}"{align}>{cell_content}</td>')

                    elif col in ['Ngày phân công', 'Thời hạn']:
                        if r == i:
                            align = ' style="text-align: center;"'
                            root_border = ' top-border-root' if i > 0 else ''
                            html_lines.append(f'<td rowspan="{root_rowspan}" class="{bg_class}{root_border}"{align}>{val}</td>')

                    elif col == 'Mã quang trắc':
                        if r == curr:
                            sub_border = ' top-border-root' if (r == i and i > 0) else (' top-border-sub' if curr > i else '')
                            cell_content = val

                            exact_code_clean = exact_code_curr.strip()
                            if partial_codes_rescheduled and (exact_code_clean in active_res_sub_codes) and code_res and not code_res['ack']:
                                res_d_fmt = format_rescheduled_display(code_res['rescheduled_deadline'])
                                is_due = code_res['is_due']
                                if is_due:
                                    tri_cls = "alert-triangle-animated"
                                    tri_html = f'&nbsp;<span class="{tri_cls}" title="Đã đến hạn ({res_d_fmt})! Xác nhận ở bảng trên để ẩn">⚠️</span>'
                                    cell_content += tri_html
                                else:
                                    tri_html = f'&nbsp;<div class="speech-bubble-wrapper" tabindex="0"><span style="font-size:16px;">⚠️</span><div class="speech-bubble-pop">Hẹn lại vào: {res_d_fmt}</div></div>'
                                    cell_content += tri_html

                            html_lines.append(f'<td rowspan="{sub_rowspan}" class="{bg_class}{sub_border}">{cell_content}</td>')

                    elif col == 'Công việc':
                        top_border_cls = ' top-border-root' if (r == i and i > 0) else (' top-border-sub' if (r == curr and curr > i) else '')
                        cell_content = val

                        if job_res and not job_res['ack']:
                            res_d_fmt = format_rescheduled_display(job_res['rescheduled_deadline'])
                            is_due = job_res['is_due']
                            if is_due:
                                tri_cls = "alert-triangle-animated"
                                tri_html = f'&nbsp;<span class="{tri_cls}" title="Đã đến hạn ({res_d_fmt})! Xác nhận ở bảng trên để ẩn">⚠️</span>'
                                cell_content += tri_html
                            else:
                                tri_html = f'&nbsp;<div class="speech-bubble-wrapper" tabindex="0"><span style="font-size:16px;">⚠️</span><div class="speech-bubble-pop">Hẹn lại vào: {res_d_fmt}</div></div>'
                                cell_content += tri_html

                        html_lines.append(f'<td class="{bg_class}{top_border_cls}">{cell_content}</td>')

                    else:
                        top_border_cls = ' top-border-root' if (r == i and i > 0) else (' top-border-sub' if (r == curr and curr > i) else '')
                        html_lines.append(f'<td class="{bg_class}{top_border_cls}">{val}</td>')

            html_lines.append('</tr>')
            curr = k

        group_idx += 1
        i = j

    html_lines.append('</tbody></table>')
    return "".join(html_lines)

def run_management_app():
    st.subheader("📊 Quản lý Thời hạn Mã Quang Trắc")

    # Đọc tệp JSON hẹn lại 1 lần duy nhất cho toàn bộ giao diện Quản lý
    res_data = load_reschedule_data()
    now_dt = datetime.now()

    # -------------------------------------------------------------------
    # KHUNG XÁC NHẬN CẢNH BÁO ĐẾN HẠN
    # -------------------------------------------------------------------
    due_codes_list = []
    due_jobs_list = []

    for c_k, c_inf in res_data.get("codes", {}).items():
        if not c_inf.get("ack", False):
            res_dt = parse_reschedule_dt(c_inf["rescheduled_deadline"])
            if res_dt and now_dt >= res_dt:
                due_codes_list.append(c_k)

    for j_k, j_inf in res_data.get("jobs", {}).items():
        if not j_inf.get("ack", False):
            res_dt = parse_reschedule_dt(j_inf["rescheduled_deadline"])
            if res_dt and now_dt >= res_dt:
                due_jobs_list.append(j_k)

    if due_codes_list or due_jobs_list:
        with st.container():
            st.info("🔔 **Có các lịch hẹn lại đã đến hạn cần xác nhận đã xem:**")
            ack_cols = st.columns(3)
            col_idx = 0
            for dc in due_codes_list:
                with ack_cols[col_idx % 3]:
                    if st.button(f"✔ Đã xem Mã: {dc}", key=f"btn_ack_code_{dc}", type="primary"):
                        ack_reschedule("code", dc)
                        st.rerun()
                col_idx += 1
            for dj in due_jobs_list:
                disp_j = dj.replace("__", " - ")
                with ack_cols[col_idx % 3]:
                    if st.button(f"✔ Đã xem Chỉ tiêu: {disp_j}", key=f"btn_ack_job_{dj}", type="primary"):
                        ack_reschedule("job", dj)
                        st.rerun()
                col_idx += 1
        st.markdown("---")

    st.markdown("""
    <div style="display: flex; flex-wrap: wrap; gap: 12px 24px; align-items: center; font-size: 14px; margin-bottom: 20px; background-color: #f8f9fa; padding: 12px 16px; border-radius: 6px; border: 1px solid #e9ecef;">
        <div style="display: inline-flex; align-items: center; white-space: nowrap;">
            <span style="display:inline-block; width:12px; height:12px; border-radius:50%; background-color:#E53E3E; margin-right:6px; flex-shrink:0;"></span>
            <b>Màu đỏ:</b>&nbsp;Quá hạn
        </div>
        <div style="display: inline-flex; align-items: center; white-space: nowrap;">
            <span style="display:inline-block; width:12px; height:12px; border-radius:50%; background-color:#D69E2E; margin-right:6px; flex-shrink:0;"></span>
            <b>Màu vàng:</b>&nbsp;Đến hạn hôm nay
        </div>
        <div style="display: inline-flex; align-items: center; white-space: nowrap;">
            <span style="display:inline-block; width:12px; height:12px; border-radius:50%; background-color:#3182CE; margin-right:6px; flex-shrink:0;"></span>
            <b>Màu xanh dương:</b>&nbsp;Đến hạn ngày mai
        </div>
        <div style="display: inline-flex; align-items: center; white-space: nowrap;">
            <span style="display:inline-block; width:12px; height:12px; border-radius:50%; background-color:#38A169; margin-right:6px; flex-shrink:0;"></span>
            <b>Màu xanh lá:</b>&nbsp;Còn hạn
        </div>
        <div style="display: inline-flex; align-items: center; white-space: nowrap;">
            <span style="font-size:16px; margin-right:4px;">⚠️</span>
            <b>Cảnh báo đến hạn:</b>&nbsp;Bấm nút xác nhận ở trên để ẩn
        </div>
    </div>
    """, unsafe_allow_html=True)

    with st.spinner("Đang tải dữ liệu từ Odoo..."):
        df_raw = fetch_management_data_from_odoo()

    if not df_raw.empty:
        df_filtered = df_raw.copy()

        if 'Công việc' in df_filtered.columns:
            df_filtered = df_filtered[
                ~df_filtered['Công việc'].astype(str).str.strip().str.lower().str.startswith('năng lượng')
            ].copy()

        # -------------------------------------------------------------------
        # KHUNG THAO TÁC HẸN LẠI THỜI HẠN
        # -------------------------------------------------------------------
        with st.expander("📅 **Chức năng Hẹn Lại Thời Hạn (Lưu nội bộ)**", expanded=False):
            res_mode = st.radio("Chọn phạm vi hẹn lại:", ["Hẹn mã quang trắc", "Hẹn chỉ tiêu"], horizontal=True)
            
            all_available_codes = sorted([c for c in df_filtered['Mã quang trắc'].unique() if c])
            
            if res_mode == "Hẹn mã quang trắc":
                selected_res_codes = st.multiselect("Chọn các Mã quang trắc cần hẹn lại:", options=all_available_codes)
                
                default_date = date.today()
                default_time = time(17, 0)
                if selected_res_codes:
                    matched_rows = df_filtered[df_filtered['Mã quang trắc'].isin(selected_res_codes)]
                    if not matched_rows.empty and 'Thời hạn' in matched_rows.columns:
                        raw_dl = str(matched_rows['Thời hạn'].iloc[0]).strip()
                        parsed_dt = parse_reschedule_dt(raw_dl)
                        if parsed_dt:
                            default_date = parsed_dt.date()
                            default_time = parsed_dt.time()

                col_d, col_t = st.columns([2, 1])
                with col_d:
                    new_res_date = st.date_input("Chọn Ngày mới:", value=default_date, format="DD/MM/YYYY")
                with col_t:
                    new_res_time = st.time_input("Chọn Giờ mới:", value=default_time)
                
                if st.button("Áp dụng Hẹn Mã Quang Trắc", type="primary"):
                    if selected_res_codes and new_res_date and new_res_time:
                        new_dt_str = f"{new_res_date.strftime('%Y-%m-%d')} {new_res_time.strftime('%H:%M:%S')}"
                        for sc in selected_res_codes:
                            orig_d = df_filtered[df_filtered['Mã quang trắc'] == sc]['Thời hạn'].iloc[0] if 'Thời hạn' in df_filtered.columns else ''
                            add_reschedule_code(sc, new_dt_str, orig_d)
                        st.success("Đã áp dụng hẹn lại thời hạn thành công!")
                        st.rerun()
                    else:
                        st.warning("Vui lòng chọn ít nhất một Mã quang trắc.")
            else:
                job_options = []
                for idx, r in df_filtered.iterrows():
                    code_val = str(r.get('Mã quang trắc', '')).strip()
                    work_val = str(r.get('Công việc', '')).strip()
                    if code_val and work_val:
                        job_options.append(f"{code_val} - {work_val}")
                job_options = sorted(list(set(job_options)))
                
                selected_res_jobs = st.multiselect("Chọn các Công việc (Chỉ tiêu) cần hẹn lại:", options=job_options)
                
                default_date = date.today()
                default_time = time(17, 0)
                if selected_res_jobs:
                    parts = selected_res_jobs[0].split(" - ", 1)
                    if len(parts) == 2:
                        c_part, w_part = parts[0].strip(), parts[1].strip()
                        matched_rows = df_filtered[(df_filtered['Mã quang trắc'] == c_part) & (df_filtered['Công việc'] == w_part)]
                        if not matched_rows.empty and 'Thời hạn' in matched_rows.columns:
                            raw_dl = str(matched_rows['Thời hạn'].iloc[0]).strip()
                            parsed_dt = parse_reschedule_dt(raw_dl)
                            if parsed_dt:
                                default_date = parsed_dt.date()
                                default_time = parsed_dt.time()

                col_d, col_t = st.columns([2, 1])
                with col_d:
                    new_res_date = st.date_input("Chọn Ngày mới:", value=default_date, format="DD/MM/YYYY")
                with col_t:
                    new_res_time = st.time_input("Chọn Giờ mới:", value=default_time)
                
                if st.button("Áp dụng Hẹn Chỉ Tiêu", type="primary"):
                    if selected_res_jobs and new_res_date and new_res_time:
                        new_dt_str = f"{new_res_date.strftime('%Y-%m-%d')} {new_res_time.strftime('%H:%M:%S')}"
                        for sj in selected_res_jobs:
                            parts = sj.split(" - ", 1)
                            if len(parts) == 2:
                                c_part, w_part = parts[0].strip(), parts[1].strip()
                                job_key = f"{c_part}__{w_part}"
                                orig_d = df_filtered[(df_filtered['Mã quang trắc'] == c_part) & (df_filtered['Công việc'] == w_part)]['Thời hạn'].iloc[0] if 'Thời hạn' in df_filtered.columns else ''
                                add_reschedule_job(job_key, new_dt_str, orig_d)
                        st.success("Đã áp dụng hẹn lại chỉ tiêu thành công!")
                        st.rerun()
                    else:
                        st.warning("Vui lòng chọn ít nhất một Công việc.")

        # -------------------------------------------------------------------
        # ÁP DỤNG THỜI HẠN NỘI BỘ VÀ TÍNH TOÁN BÁO ĐỘNG
        # -------------------------------------------------------------------
        df_filtered['_code_reschedule'] = None
        df_filtered['_job_reschedule'] = None
        df_filtered['_is_reschedule_due'] = False

        for idx, row in df_filtered.iterrows():
            r_code = str(row.get('Mã quang trắc', '')).strip()
            r_work = str(row.get('Công việc', '')).strip()
            j_key = f"{r_code}__{r_work}"

            if r_code in res_data.get("codes", {}):
                c_info = res_data["codes"][r_code]
                res_dt = parse_reschedule_dt(c_info["rescheduled_deadline"])
                if res_dt:
                    is_due = (now_dt >= res_dt) and (not c_info.get("ack", False))
                    df_filtered.at[idx, '_code_reschedule'] = {
                        "original_deadline": c_info["original_deadline"],
                        "rescheduled_deadline": c_info["rescheduled_deadline"],
                        "ack": c_info.get("ack", False),
                        "is_due": is_due
                    }
                    if is_due:
                        df_filtered.at[idx, '_is_reschedule_due'] = True
                    if not c_info.get("ack", False):
                        df_filtered.at[idx, 'deadline_dt'] = pd.to_datetime(res_dt)

            if j_key in res_data.get("jobs", {}):
                j_info = res_data["jobs"][j_key]
                res_dt = parse_reschedule_dt(j_info["rescheduled_deadline"])
                if res_dt:
                    is_due = (now_dt >= res_dt) and (not j_info.get("ack", False))
                    df_filtered.at[idx, '_job_reschedule'] = {
                        "original_deadline": j_info["original_deadline"],
                        "rescheduled_deadline": j_info["rescheduled_deadline"],
                        "ack": j_info.get("ack", False),
                        "is_due": is_due
                    }
                    if is_due:
                        df_filtered.at[idx, '_is_reschedule_due'] = True
                    if not j_info.get("ack", False):
                        df_filtered.at[idx, 'deadline_dt'] = pd.to_datetime(res_dt)

        df_filtered = df_filtered.sort_values(
            by=['_is_reschedule_due', 'deadline_dt', 'id'], 
            ascending=[False, True, True], 
            na_position='last'
        ).reset_index(drop=True)

        today_date = date.today()
        def update_status_circle(row):
            deadline_dt = row.get('deadline_dt')
            if pd.isna(deadline_dt):
                return '<div style="text-align: center;"><span style="display:inline-block; width:14px; height:14px; border-radius:50%; background-color:#CBD5E0;" title="Không có thời hạn"></span></div>'
            d_date = deadline_dt.date()
            diff_days = (d_date - today_date).days
            if diff_days < 0:
                color = "#E53E3E"
                title = f"Quá hạn ({abs(diff_days)} ngày)"
            elif diff_days == 0:
                color = "#D69E2E"
                title = "Đến hạn hôm nay"
            elif diff_days == 1:
                color = "#3182CE"
                title = "Đến hạn ngày mai"
            else:
                color = "#38A169"
                title = f"Còn hạn ({diff_days} ngày)"
            return f'<div style="text-align: center;"><span style="display:inline-block; width:14px; height:14px; border-radius:50%; background-color:{color};" title="{title}"></span></div>'

        df_filtered['Trạng thái hạn'] = df_filtered.apply(update_status_circle, axis=1)

        # -------------------------------------------------------------------
        # KHUNG LỌC GIAO DIỆN
        # -------------------------------------------------------------------
        with st.container():
            col1, col2, col3, col4, col5, col6 = st.columns(6)

            with col1:
                filter_start_date = st.date_input("Ngày phân công", value=None, format="DD/MM/YYYY")

            with col2:
                filter_deadline = st.date_input("Thời hạn", value=None, format="DD/MM/YYYY")

            all_codes = sorted([c for c in df_filtered['Mã quang trắc'].unique() if c]) if 'Mã quang trắc' in df_filtered.columns else []
            all_works = sorted([w for w in df_filtered['Công việc'].unique() if w]) if 'Công việc' in df_filtered.columns else []
            all_methods = sorted([m for m in df_filtered['Testing method'].unique() if m]) if 'Testing method' in df_filtered.columns else []
            
            assignees_set = set()
            if 'Người được phân công' in df_filtered.columns:
                for val in df_filtered['Người được phân công'].dropna():
                    for p in str(val).split(','):
                        p_clean = p.strip()
                        if p_clean:
                            assignees_set.add(p_clean)
            all_assignees = sorted(list(assignees_set))

            with col3:
                selected_codes = st.multiselect("Mã quang trắc", options=all_codes, placeholder="Tất cả mã")

            with col4:
                selected_works = st.multiselect("Công việc", options=all_works, placeholder="Tất cả công việc")

            with col5:
                selected_methods = st.multiselect("Testing method", options=all_methods, placeholder="Tất cả phương pháp")

            with col6:
                selected_assignees = st.multiselect("Người được phân công", options=all_assignees, placeholder="Tất cả người làm")

        if filter_start_date and filter_deadline:
            df_filtered = df_filtered[
                (df_filtered['start_date_dt'].dt.date >= filter_start_date) & 
                (df_filtered['deadline_dt'].dt.date <= filter_deadline)
            ]
        elif filter_start_date:
            df_filtered = df_filtered[df_filtered['start_date_dt'].dt.date == filter_start_date]
        elif filter_deadline:
            df_filtered = df_filtered[df_filtered['deadline_dt'].dt.date == filter_deadline]

        if selected_codes:
            df_filtered = df_filtered[df_filtered['Mã quang trắc'].isin(selected_codes)]

        if selected_works:
            df_filtered = df_filtered[df_filtered['Công việc'].isin(selected_works)]

        if selected_methods:
            df_filtered = df_filtered[df_filtered['Testing method'].isin(selected_methods)]

        if selected_assignees:
            def match_assignees(val):
                val_str = str(val)
                return any(assignee in val_str for assignee in selected_assignees)
            df_filtered = df_filtered[df_filtered['Người được phân công'].apply(match_assignees)]

        if 'ĐVT' in df_filtered.columns:
            df_filtered = df_filtered[
                df_filtered['ĐVT'].astype(str).str.strip().ne('') & 
                df_filtered['ĐVT'].astype(str).str.strip().ne('False')
            ].copy()

        if not df_filtered.empty:
            html_table = render_management_html_table(df_filtered, res_data)
            st.markdown(
                f'<div style="overflow-x: auto; width: 100%;" translate="no">{html_table}</div>', 
                unsafe_allow_html=True
            )
        else:
            st.warning("Không tìm thấy dữ liệu phù hợp với điều kiện lọc.")
    else:
        st.info("Không tìm thấy mã quang trắc nào được phân công hoặc hệ thống đang bận.")


# ===================================================================
# 4. ĐIỀU HƯỚNG VÀ GIAO DIỆN CHÍNH (MAIN ROUTER)
# ===================================================================
if 'app_mode' not in st.session_state:
    st.session_state['app_mode'] = None

if st.session_state['app_mode'] is not None:
    col_back, col_space = st.columns([2, 10])
    with col_back:
        if st.button("⬅️ Quay lại trang chủ", use_container_width=True):
            st.session_state['app_mode'] = None
            st.rerun()

if st.session_state['app_mode'] is None:
    st.title("Hệ thống Quản lý & Tra cứu Mã Quang Trắc")
    st.write("Vui lòng chọn chức năng bạn muốn sử dụng:")
    st.markdown("---")

    col_btn1, col_btn2 = st.columns(2)

    with col_btn1:
        st.info("### 🔍 Tra cứu")
        st.write("Tìm kiếm công việc chi tiết theo từng **Mã quang trắc** cụ thể.")
        if st.button("Truy cập Tra cứu", use_container_width=True, type="primary"):
            st.session_state['app_mode'] = 'tracuu'
            st.rerun()

    with col_btn2:
        st.success("### 📊 Quản lý")
        st.write("Tổng hợp danh sách công việc theo **Thời hạn** và bộ lọc đa năng.")
        if st.button("Truy cập Quản lý", use_container_width=True, type="primary"):
            st.session_state['app_mode'] = 'quanly'
            st.rerun()

elif st.session_state['app_mode'] == 'tracuu':
    run_lookup_app()

elif st.session_state['app_mode'] == 'quanly':
    run_management_app()