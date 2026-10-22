import streamlit as st
import xmlrpc.client
import pandas as pd
from datetime import datetime, date, time
import time as time_lib
import json
import os
import ssl
import re
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


# ===================================================================
# 3. CHỨC NĂNG 2: QUẢN LÝ & TRÍCH XUẤT PHIẾU TỔNG HỢP RÚT GỌN
# ===================================================================
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
            df['start_date_dt'] = pd.to_datetime(df[start_date_col], errors='coerce')

            if 'start_date_dt' in df.columns:
                df = df[df['start_date_dt'] >= pd.to_datetime('2026-09-01')].copy()

            if df.empty:
                return pd.DataFrame()

            df['Mẫu'] = df.apply(lambda r: extract_sample_value(r, active_sample_fields), axis=1)

            for col in df.columns:
                if col not in ['deadline_dt', 'start_date_dt', 'Trạng thái hạn', 'assignee_ids', 'Mẫu']:
                    df[col] = df[col].apply(clean_odoo_field_value)

            df = df.rename(columns=actual_fields_map)

            if 'Mã quang trắc' in df.columns:
                df = df[df['Mã quang trắc'].astype(str).str.strip().ne('')].copy()

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
                                    if 'dumas' in r_method and r_code in codes_with_completed_dumas:
                                        return False
                                    return True
                                
                                df = df[df.apply(filter_kd_dumas, axis=1)].copy()
                        except Exception:
                            pass

            if 'start_date_dt' in df.columns and 'Ngày phân công' in df.columns:
                df['Ngày phân công'] = df['start_date_dt'].dt.strftime('%d/%m/%Y').fillna('')
            if 'deadline_dt' in df.columns and 'Thời hạn' in df.columns:
                df['Thời hạn'] = df['deadline_dt'].dt.strftime('%d/%m/%Y').fillna('')

            if 'Mã quang trắc' in df.columns:
                df['_group_code'] = df['Mã quang trắc'].astype(str).str.split('.').str[0].str.strip()
            else:
                df['_group_code'] = df.index.astype(str)

            df = df.sort_values(
                by=['deadline_dt', '_group_code', 'id'], 
                ascending=[True, True, True], 
                na_position='last'
            ).reset_index(drop=True)

            return df

        except Exception as e:
            if attempt < max_retries - 1:
                time_lib.sleep(1)
                continue
            else:
                st.error(f"Lỗi kết nối hoặc truy vấn Odoo: {e}")
                return pd.DataFrame()

def render_management_html_table(df):
    if df.empty:
        return ""

    cols_order = [
        'Trạng thái hạn', 'Ngày phân công', 'Thời hạn',
        'Mẫu', 'Mã quang trắc', 'Công việc', 'Testing method', 'ĐVT', 'Ghi chú'
    ]
    existing_cols = [c for c in cols_order if c in df.columns]

    col_widths = {
        'Trạng thái hạn': '60px',
        'Ngày phân công': '100px',
        'Thời hạn': '100px',
        'Mẫu': '180px',
        'Mã quang trắc': '160px',
        'Công việc': '220px',
        'Testing method': '220px',
        'ĐVT': '70px',
        'Ghi chú': '100px',
        'Thao tác': '90px'
    }

    html_lines = []
    html_lines.append('<style>')
    html_lines.append('  .custom-table-mgmt { width: 100%; border-collapse: collapse !important; margin-top: 15px; font-family: sans-serif; font-size: 14px; table-layout: fixed; }')
    html_lines.append('  .custom-table-mgmt th { background-color: #E2E8F0; color: #1A202C; font-weight: bold; text-align: center; border: 1px solid #CBD5E0; padding: 10px 8px; }')
    html_lines.append('  .custom-table-mgmt td { border: 1px solid #CBD5E0; padding: 8px 8px; text-align: left; vertical-align: middle; word-wrap: break-word; overflow-wrap: break-word; }')
    html_lines.append('  .top-border-root { border-top: 2.5px solid #2D3748 !important; }')
    html_lines.append('  .top-border-sub { border-top: 1.5px solid #718096 !important; }')
    html_lines.append('  .bg-group-0 { background-color: #FFFFFF !important; }')
    html_lines.append('  .bg-group-1 { background-color: #EDF2F7 !important; }')
    html_lines.append('  .btn-ignore { background-color: #FFF; border: 1px solid #CBD5E0; border-radius: 6px; padding: 6px 10px; cursor: pointer; color: #E53E3E; font-weight: bold; font-size: 13px; }')
    html_lines.append('  .btn-ignore:hover { background-color: #FED7D7; }')
    html_lines.append('</style>')

    html_lines.append('<table class="custom-table-mgmt"><thead><tr>')
    for col in existing_cols:
        header_title = "" if col == 'Trạng thái hạn' else col
        width_style = f' style="width: {col_widths.get(col, "auto")};"'
        html_lines.append(f'<th{width_style}>{header_title}</th>')
    html_lines.append(f'<th style="width: {col_widths["Thao tác"]};">Thao tác</th>')
    html_lines.append('</tr></thead><tbody>')

    n = len(df)
    i = 0
    group_idx = 0

    while i < n:
        j = i + 1
        root_code_i = df.iloc[i]['_group_code']
        while j < n and df.iloc[j]['_group_code'] == root_code_i:
            j += 1
        root_rowspan = j - i

        bg_class = f"bg-group-{group_idx % 2}"

        curr = i
        while curr < j:
            k = curr + 1
            exact_code_curr = str(df.iloc[curr]['Mã quang trắc']) if 'Mã quang trắc' in existing_cols else ''
            while k < j and str(df.iloc[k]['Mã quang trắc']) == exact_code_curr:
                k += 1
            sub_rowspan = k - curr

            for r in range(curr, k):
                html_lines.append('<tr>')
                row_raw = df.iloc[r]
                job_id = row_raw.get('id')

                for col in existing_cols:
                    val = str(df.iloc[r][col])

                    if col in ['Trạng thái hạn', 'Ngày phân công', 'Thời hạn']:
                        if r == i:
                            align = ' style="text-align: center;"'
                            root_border = ' top-border-root' if i > 0 else ''
                            html_lines.append(f'<td rowspan="{root_rowspan}" class="{bg_class}{root_border}"{align}>{val}</td>')

                    elif col in ['Mẫu', 'Mã quang trắc']:
                        if r == curr:
                            sub_border = ' top-border-root' if (r == i and i > 0) else (' top-border-sub' if curr > i else '')
                            html_lines.append(f'<td rowspan="{sub_rowspan}" class="{bg_class}{sub_border}">{val}</td>')

                    else:
                        top_border_cls = ' top-border-root' if (r == i and i > 0) else (' top-border-sub' if (r == curr and curr > i) else '')
                        html_lines.append(f'<td class="{bg_class}{top_border_cls}">{val}</td>')

                top_border_cls = ' top-border-root' if (r == i and i > 0) else (' top-border-sub' if (r == curr and curr > i) else '')
                btn_url = f"?ignore_job_id={job_id}"
                btn_html = f'<a href="{btn_url}" target="_self" onclick="return confirm(\'Có muốn bỏ qua công việc này?\');" style="text-decoration:none;"><button class="btn-ignore">🚫 Bỏ qua</button></a>'
                html_lines.append(f'<td class="{bg_class}{top_border_cls}" style="text-align: center;">{btn_html}</td>')

                html_lines.append('</tr>')
            curr = k

        group_idx += 1
        i = j

    html_lines.append('</tbody></table>')
    return "".join(html_lines)


# ===================================================================
# LOGIC RÚT GỌN CÔNG VIỆC CHUẨN THEO PHẢN HỒI MỚI NHẤT
# ===================================================================
def get_short_method_name(method_str):
    """Lấy ngắn gọn tên phương pháp thử nghiệm"""
    if not method_str:
        return ""
    m = str(method_str).strip().lower()
    if 'dumas' in m:
        return 'Dumas'
    elif 'kjeldahl' in m or 'kejhdal' in m or 'kjel' in m:
        return 'Kjeldahl'
    elif 'sắc ký' in m or 'chromatography' in m:
        return 'SK'
    elif 'enzym' in m or 'enzyme' in m:
        return 'Enzym'
    else:
        code_match = re.search(r'(\d+:\d{4})', str(method_str))
        if code_match:
            return code_match.group(1)
        upper_matches = re.findall(r'[A-Z0-9]+', str(method_str))
        if upper_matches:
            return upper_matches[0]
        return str(method_str).strip().split()[0].capitalize()

def get_short_work_name(work_str, method_str=""):
    """Bảng quy tắc rút gọn tên công việc chính xác"""
    if not work_str:
        return ""
    
    w = str(work_str).strip()
    w_lower = w.lower()
    m_str = str(method_str).strip()

    if 'nitơ axit amin so với' in w_lower or 'nito axit amin so voi' in w_lower:
        return None
    if 'nitơ amoniac so với' in w_lower or 'nito amoniac so voi' in w_lower:
        return None

    if 'aoac' in m_str.lower() and ('996.06' in m_str.lower() or 'acid béo' in w_lower or 'axit béo' in w_lower):
        return 'ab'

    if 'protein' in w_lower or 'đạm' in w_lower:
        m_short = get_short_method_name(m_str)
        return f"Pro{m_short}"

    if 'carbohydrate không bao gồm' in w_lower or 'carbohydrat không bao gồm' in w_lower:
        return 'Carbo'
    elif 'carbohydrat (*)' in w_lower or 'carbohydrate (*)' in w_lower or 'carbohydrat ⁽*⁾' in w_lower:
        return 'Carbo(*)'

    if 'hàm lượng chất béo' in w_lower or 'chất béo' in w_lower:
        if 'bão hòa' in w_lower:
            return 'BeoBH'
        elif 'vắt mì' in w_lower:
            return 'BeoVat'
        elif 'trans' in w_lower:
            return 'BeoTrans'
        elif 'mạch trung bình' in w_lower or 'mct' in w_lower:
            return 'MCT'
        elif 'không bão hòa đa' in w_lower or 'pufa' in w_lower:
            return 'PUFA'
        elif 'không bão hòa đơn' in w_lower or 'mufa' in w_lower:
            return 'MUFA'
        else:
            return 'Beo'

    if 'đường tổng số' in w_lower:
        return 'TongSK'
    elif 'đường tổng' in w_lower:
        return 'Tong'
    elif 'đường khử tự do' in w_lower:
        return 'KhuTD'
    elif 'đường khử' in w_lower:
        return 'Khu'
    elif 'lactose monohydrate' in w_lower:
        return 'lacmono'

    if 'xơ tổng số (tdf)' in w_lower or 'tdf' in w_lower:
        return 'TDF'
    elif 'xơ tiêu hóa' in w_lower:
        return 'XoTH'
    elif 'xơ thực phẩm tổng' in w_lower:
        return 'XoTP'
    elif 'tinh bột' in w_lower:
        return 'tb'

    if 'nito amoniac' in w_lower or 'nitơ amoniac' in w_lower or 'nh3' in w_lower:
        return 'NH3'
    elif 'nitơ amin amoniac' in w_lower or 'nitơ formon' in w_lower or 'nito formon' in w_lower:
        return 'nitofm'
    elif 'nitơ axit amin' in w_lower or 'nito axit amin' in w_lower:
        return 'naa'
    elif 'nito tổng' in w_lower or 'nitơ tổng' in w_lower:
        return 'Nito'

    if 'docosahexaenoic acid' in w_lower or 'dha' in w_lower:
        return 'DHA'
    elif 'omega 3' in w_lower or 'omg 3' in w_lower or 'α-linolenic acid' in w_lower:
        return 'Omg3'
    elif 'omega 6' in w_lower or 'omg 6' in w_lower:
        return 'Omg6'
    elif 'omega 9' in w_lower or 'omg 9' in w_lower:
        return 'Omg9'
    elif 'axit hydrocyanic' in w_lower or 'hcn' in w_lower:
        return 'HCN'
    elif 'độ ẩm vắt' in w_lower or 'ẩm vắt' in w_lower:
        return 'AmVat'
    elif 'độ ẩm' in w_lower or 'ẩm' in w_lower:
        return 'Am'
    elif 'chất khô' in w_lower:
        return 'CK'
    elif 'tro' in w_lower:
        return 'Tro'

    if 'quatest3 1222:2024' in m_str.lower() or 'aoac 2018.16' in m_str.lower():
        cleaned = re.sub(r'^(hàm lượng|ham luong)\s*', '', w, flags=re.IGNORECASE)
        return cleaned.strip().capitalize()

    cleaned = re.sub(r'^(hàm lượng|ham luong)\s*', '', w, flags=re.IGNORECASE)
    return cleaned.strip()

def generate_summary_print_table(df_filtered):
    """Trích xuất bảng dữ liệu Mã quang trắc - Công việc rút gọn chuẩn in A4"""
    if df_filtered.empty or 'Mã quang trắc' not in df_filtered.columns:
        return ""

    summary_data = []
    grouped = df_filtered.groupby('Mã quang trắc', sort=False)

    for code, group in grouped:
        works_in_code = []
        short_names_count = {}
        row_short_map = []

        for _, row in group.iterrows():
            w_name = row.get('Công việc', '')
            m_name = row.get('Testing method', '')
            
            s_name = get_short_work_name(w_name, m_name)
            
            if s_name is None:
                continue
                
            short_names_count[s_name] = short_names_count.get(s_name, 0) + 1
            row_short_map.append((s_name, m_name))

        for s_name, m_name in row_short_map:
            if s_name == 'Am' and short_names_count.get('Am', 0) > 1:
                m_short = get_short_method_name(m_name)
                final_work_str = f"Am{m_short}"
            elif short_names_count.get(s_name, 0) > 1 and not any(s_name.startswith(p) for p in ['Pro', 'Am', 'ab']):
                m_short = get_short_method_name(m_name)
                final_work_str = f"{s_name}{m_short}"
            else:
                final_work_str = s_name
                
            works_in_code.append(final_work_str)

        if not works_in_code:
            continue

        combined_works = ", ".join(dict.fromkeys(works_in_code))
        sample_name = group.iloc[0].get('Mẫu', '')
        
        summary_data.append({
            'Mã quang trắc': code,
            'Mẫu': sample_name,
            'Công việc': combined_works
        })

    if not summary_data:
        return ""

    df_summary = pd.DataFrame(summary_data)

    html_lines = []
    html_lines.append('<style>')
    html_lines.append('  .summary-table { width: 100%; border-collapse: collapse; margin-top: 10px; font-family: sans-serif; font-size: 13px; table-layout: fixed; }')
    html_lines.append('  .summary-table th, .summary-table td { border: 1px solid #1A202C; padding: 6px 10px; text-align: left; vertical-align: middle; word-wrap: break-word; overflow-wrap: break-word; }')
    html_lines.append('  .summary-table th { background-color: #EDF2F7; font-weight: bold; }')
    html_lines.append('  .summary-table tr:nth-child(even) { background-color: #F8FAFC; }')
    html_lines.append('</style>')

    # ĐIỀU CHỈNH ĐỘ RỘNG CỘT & ĐỔI TÊN TIÊU ĐỀ THÀNH "Công việc"
    html_lines.append('<table id="table-to-print" class="summary-table"><thead><tr>')
    html_lines.append('<th style="width: 45px; text-align: center;">STT</th>')
    html_lines.append('<th style="width: 150px;">Mã quang trắc</th>')
    html_lines.append('<th style="width: 400px;">Mẫu</th>')
    html_lines.append('<th>Công việc</th>')
    html_lines.append('</tr></thead><tbody>')

    for idx, row in df_summary.iterrows():
        html_lines.append('<tr>')
        html_lines.append(f'<td style="text-align: center;">{idx + 1}</td>')
        html_lines.append(f'<td><b>{row["Mã quang trắc"]}</b></td>')
        html_lines.append(f'<td>{row["Mẫu"]}</td>')
        html_lines.append(f'<td><b>{row["Công việc"]}</b></td>')
        html_lines.append('</tr>')

    html_lines.append('</tbody></table>')
    
    return "".join(html_lines)


def run_management_app():
    col_title, col_reload = st.columns([8, 2])
    with col_title:
        st.subheader("📊 Quản lý Thời hạn Mã Quang Trắc")
    with col_reload:
        if st.button("🔄 Tải lại dữ liệu Odoo", use_container_width=True):
            if 'mgmt_df_raw' in st.session_state:
                del st.session_state['mgmt_df_raw']
            st.rerun()

    query_params = st.query_params
    if "ignore_job_id" in query_params:
        job_id_to_ignore = query_params["ignore_job_id"]
        save_ignored_job(job_id_to_ignore)
        st.query_params.clear()
        st.rerun()

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
    </div>
    """, unsafe_allow_html=True)

    if 'mgmt_df_raw' not in st.session_state:
        with st.spinner("Đang kết nối và tải toàn bộ dữ liệu từ Odoo..."):
            st.session_state['mgmt_df_raw'] = fetch_management_data_from_odoo()

    df_raw = st.session_state['mgmt_df_raw']

    if not df_raw.empty:
        df_filtered = df_raw.copy()

        ignored_jobs = load_ignored_jobs()
        if ignored_jobs and 'id' in df_filtered.columns:
            df_filtered = df_filtered[~df_filtered['id'].astype(str).isin(ignored_jobs)].copy()

        if df_filtered.empty:
            st.info("Tất cả công việc đã được hoàn thành hoặc bỏ qua.")
            return

        if 'Công việc' in df_filtered.columns:
            df_filtered = df_filtered[
                ~df_filtered['Công việc'].astype(str).str.strip().str.lower().str.startswith('năng lượng')
            ].copy()

        # BỘ LỌC CỰC NHANH TRÊN MEMORY RAM
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

        # THANH CÔNG CỤ XUẤT / IN PHIẾU TỔNG HỢP
        col_summary_btn, _ = st.columns([3, 9])
        with col_summary_btn:
            show_print_view = st.checkbox("📄 Hiển thị Phiếu tổng hợp để in", value=False)

        if show_print_view:
            st.markdown("---")
            summary_html = generate_summary_print_table(df_filtered)
            if summary_html:
                components.html(
                    """
                    <div style="margin-bottom: 10px;">
                        <button onclick="
                            var pWin = window.open('', '', 'width=950,height=650');
                            var tableHtml = window.parent.document.getElementById('table-to-print').outerHTML;
                            pWin.document.write('<html><head><title>In Bảng Dữ Liệu</title>');
                            pWin.document.write('<style>');
                            pWin.document.write('@page { size: A4 portrait; margin: 10mm; }');
                            pWin.document.write('body { margin: 0; padding: 0; font-family: sans-serif; }');
                            pWin.document.write('.summary-table { width: 100%; border-collapse: collapse; font-size: 13px; table-layout: fixed; }');
                            pWin.document.write('.summary-table th, .summary-table td { border: 1px solid #1A202C; padding: 6px 10px; text-align: left; vertical-align: middle; word-wrap: break-word; }');
                            pWin.document.write('.summary-table th { background-color: #EDF2F7; font-weight: bold; }');
                            pWin.document.write('.summary-table tr:nth-child(even) { background-color: #F8FAFC; }');
                            pWin.document.write('</style></head><body>');
                            pWin.document.write(tableHtml);
                            pWin.document.write('</body></html>');
                            pWin.document.close();
                            pWin.focus();
                            setTimeout(function(){ pWin.print(); pWin.close(); }, 300);
                        " style="background-color: #2563EB; color: white; border: none; padding: 10px 20px; border-radius: 6px; font-weight: bold; font-size: 14px; cursor: pointer;">
                            🖨️ In / Xuất PDF Phiếu Tổng Hợp
                        </button>
                    </div>
                    """,
                    height=55
                )
                st.markdown(summary_html, unsafe_allow_html=True)
            else:
                st.warning("Không có dữ liệu để tạo phiếu tổng hợp.")
            st.markdown("---")
            
        if not df_filtered.empty:
            html_table = render_management_html_table(df_filtered)
            st.markdown(
                f'<div style="overflow-x: auto; width: 100%;" translate="no">{html_table}</div>', 
                unsafe_allow_html=True
            )
        else:
            st.warning("Không tìm thấy dữ liệu phù hợp với điều kiện lọc.")
    else:
        st.info("Không tìm thấy mã quang trắc nào được phân công từ ngày 01/09/2026.")


# ===================================================================
# 4. ĐIỀU HƯỚNG VÀ GIAO DIỆN CHÍNH
# ===================================================================
if 'app_mode' not in st.session_state:
    st.session_state['app_mode'] = None

if st.session_state['app_mode'] is not None:
    col_back, col_space = st.columns([2, 10])
    with col_back:
        if st.button("⬅️ Quay lại trang chủ", use_container_width=True):
            st.session_state['app_mode'] = None
            if 'mgmt_df_raw' in st.session_state:
                del st.session_state['mgmt_df_raw']
            st.rerun()

if st.session_state['app_mode'] is None:
    st.title("Hệ thống Quản lý & Tra cứu Mã Quang Trắc")
    st.write("Vui lòng chọn chức năng bạn muốn sử dụng:")
    st.markdown("---")

    col_btn1, col_btn2 = st.columns(2)

    with col_btn1:
        st.info("### 🔍 Tra cứu")
        st.write("Tìm kiếm công việc chi tiết theo từng **Mã quang trắc** cụ thể (Tra cứu toàn bộ thời gian).")
        if st.button("Truy cập Tra cứu", use_container_width=True, type="primary"):
            st.session_state['app_mode'] = 'tracuu'
            st.rerun()

    with col_btn2:
        st.success("### 📊 Quản lý")
        st.write("Tổng hợp danh sách công việc theo **Thời hạn** từ 01/09/2026 trở đi.")
        if st.button("Truy cập Quản lý", use_container_width=True, type="primary"):
            st.session_state['app_mode'] = 'quanly'
            st.rerun()

elif st.session_state['app_mode'] == 'tracuu':
    run_lookup_app()

elif st.session_state['app_mode'] == 'quanly':
    run_management_app()
