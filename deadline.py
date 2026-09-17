import streamlit as st
import xmlrpc.client
import pandas as pd
from datetime import datetime, date
import time

# -------------------------------------------------------------------
# 1. CẤU HÌNH KẾT NỐI ODOO
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

def clean_odoo_field_value(x):
    if not x:
        return ''
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

# -------------------------------------------------------------------
# 2. HÀM TRUY VẤN DỮ LIỆU CÓ CACHE VÀ RETRY
# -------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def fetch_data_from_odoo():
    max_retries = 3
    for attempt in range(max_retries):
        try:
            common = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/common', allow_none=True)
            uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
            models = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/object', allow_none=True)

            user_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'res.users', 'search', [[['name', 'ilike', 'N Văn Phương']]])
            partner_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'res.partner', 'search', [[['name', 'ilike', 'N Văn Phương']]])
            valid_ids = set(user_ids + partner_ids)

            if not valid_ids:
                return pd.DataFrame()

            existing_fields_info = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'assign.work.line', 'fields_get', [], {'attributes': ['string']}
            )
            valid_odoo_fields = set(existing_fields_info.keys())

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

            df = df.sort_values(by=['deadline_dt', 'id'], ascending=[True, True], na_position='last').reset_index(drop=True)

            today = date.today()
            def get_status_circle(deadline_dt):
                if pd.isna(deadline_dt):
                    return '<div style="text-align: center;"><span style="display:inline-block; width:14px; height:14px; border-radius:50%; background-color:#CBD5E0;" title="Không có thời hạn"></span></div>'
                d_date = deadline_dt.date()
                diff_days = (d_date - today).days
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

            df['Trạng thái hạn'] = df['deadline_dt'].apply(get_status_circle)

            for col in df.columns:
                if col not in ['deadline_dt', 'start_date_dt', 'Trạng thái hạn', 'assignee_ids']:
                    df[col] = df[col].apply(clean_odoo_field_value)

            df = df.rename(columns=actual_fields_map)

            if 'Mã quang trắc' in df.columns:
                df = df[df['Mã quang trắc'].astype(str).str.strip().ne('')].copy()

            if 'start_date_dt' in df.columns and 'Ngày phân công' in df.columns:
                df['Ngày phân công'] = df['start_date_dt'].dt.strftime('%d/%m/%Y').fillna('')
            if 'deadline_dt' in df.columns and 'Thời hạn' in df.columns:
                df['Thời hạn'] = df['deadline_dt'].dt.strftime('%d/%m/%Y').fillna('')

            return df

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            else:
                st.error(f"Lỗi kết nối hoặc truy vấn Odoo (Máy chủ phản hồi quá tải/502): {e}")
                return pd.DataFrame()

# -------------------------------------------------------------------
# 3. HIỂN THỊ BẢNG HTML
# -------------------------------------------------------------------
def render_merged_html_table(df):
    if df.empty:
        return ""

    cols_order = [
        'Trạng thái hạn', 'Ngày phân công', 'Thời hạn', 
        'Mã quang trắc', 'Công việc', 'Testing method', 'ĐVT', 'Ghi chú'
    ]
    existing_cols = [c for c in cols_order if c in df.columns]
    df = df[existing_cols].fillna('')

    if 'Mã quang trắc' in df.columns:
        df['_group_code'] = df['Mã quang trắc'].apply(lambda x: str(x).split('.')[0].strip() if x else '')
    else:
        df['_group_code'] = df.index

    date_cols = [c for c in ['Trạng thái hạn', 'Ngày phân công', 'Thời hạn'] if c in existing_cols]

    html_lines = []
    html_lines.append('<style>')
    html_lines.append('  .custom-table { width: 100%; border-collapse: collapse !important; margin-top: 15px; font-family: sans-serif; font-size: 14px; }')
    html_lines.append('  .custom-table th { background-color: #E2E8F0; color: #1A202C; font-weight: bold; text-align: center; border: 1px solid #CBD5E0; padding: 10px 12px; }')
    html_lines.append('  .custom-table td { border-left: 1px solid #CBD5E0; border-right: 1px solid #CBD5E0; border-top: 1px solid #CBD5E0; border-bottom: 1px solid #CBD5E0; padding: 8px 12px; text-align: left; vertical-align: top; }')
    html_lines.append('  .top-border-root { border-top: 2.5px solid #2D3748 !important; }')
    html_lines.append('  .top-border-sub { border-top: 1.5px solid #718096 !important; }')
    html_lines.append('  .bg-group-0 { background-color: #FFFFFF !important; }')
    html_lines.append('  .bg-group-1 { background-color: #EDF2F7 !important; }')
    html_lines.append('</style>')

    html_lines.append('<table class="custom-table"><thead><tr>')
    for col in existing_cols:
        header_title = "" if col == 'Trạng thái hạn' else col
        html_lines.append(f'<th>{header_title}</th>')
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
                
                is_first_row_of_root = (r == i)
                is_first_row_of_sub = (r == curr)

                if is_first_row_of_root and i > 0:
                    top_border_cls = ' top-border-root'
                elif is_first_row_of_sub and curr > i:
                    top_border_cls = ' top-border-sub'
                else:
                    top_border_cls = ''

                for col in existing_cols:
                    val = str(df.iloc[r][col])

                    if col in date_cols:
                        if r == i:
                            align = ' style="text-align: center;"'
                            root_border = ' top-border-root' if i > 0 else ''
                            html_lines.append(f'<td rowspan="{root_rowspan}" class="{bg_class}{root_border}"{align}>{val}</td>')

                    elif col == 'Mã quang trắc':
                        if r == curr:
                            sub_border = ' top-border-root' if (r == i and i > 0) else (' top-border-sub' if curr > i else '')
                            html_lines.append(f'<td rowspan="{sub_rowspan}" class="{bg_class}{sub_border}">{val}</td>')

                    else:
                        html_lines.append(f'<td class="{bg_class}{top_border_cls}">{val}</td>')

            html_lines.append('</tr>')
            curr = k

        group_idx += 1
        i = j

    html_lines.append('</tbody></table>')
    return "".join(html_lines)

# -------------------------------------------------------------------
# 4. GIAO DIỆN STREAMLIT
# -------------------------------------------------------------------
import streamlit.components.v1 as components

st.set_page_config(page_title="Quản lý Thời hạn Quang Trắc", layout="wide")

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

st.title("Quản lý Thời hạn Mã Quang Trắc")

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

with st.spinner("Đang tải dữ liệu từ Odoo..."):
    df_raw = fetch_data_from_odoo()

if not df_raw.empty:
    df_filtered = df_raw.copy()

    if 'Công việc' in df_filtered.columns:
        df_filtered = df_filtered[
            ~df_filtered['Công việc'].astype(str).str.strip().str.lower().str.startswith('năng lượng')
        ].copy()

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

    # LOGIC LỌC THEO NGÀY PHÂN CÔNG VÀ THỜI HẠN
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
        html_table = render_merged_html_table(df_filtered)
        st.markdown(f'<div translate="no">{html_table}</div>', unsafe_allow_html=True)
    else:
        st.warning("Không tìm thấy dữ liệu phù hợp với điều kiện lọc.")
else:
    st.info("Không tìm thấy mã quang trắc nào được phân công hoặc hệ thống đang bận.")