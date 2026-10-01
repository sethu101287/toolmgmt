"""Oracle DB connection helpers and all instance/patch/report queries."""
import logging
import os

try:
    import winreg
except ImportError:
    winreg = None

logger = logging.getLogger(__name__)

try:
    import oracledb
except ImportError:
    oracledb = None

from .appcore import (
    STAGE_NAME_TO_CODE,
    ORACLE_DEFAULT_HOST,
    ORACLE_DEFAULT_PORT,
    ORACLE_DEFAULT_SERVICE,
    ORACLE_DEFAULT_USER,
)
from .nlu import parse_patch_seq_parts


def _get_windows_user_env(name):
    if os.name != 'nt' or winreg is None:
        return None

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Environment') as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value.strip() if isinstance(value, str) else value
    except OSError:
        return None


def get_oracle_connection():
    if oracledb is None:
        return None, 'Python package "oracledb" is not installed. Install it with: python -m pip install oracledb'

    oracle_user = os.getenv('ORACLE_USER', ORACLE_DEFAULT_USER)
    oracle_password = os.getenv('ORACLE_PASSWORD') or _get_windows_user_env('ORACLE_PASSWORD')
    oracle_host = os.getenv('ORACLE_HOST', ORACLE_DEFAULT_HOST)
    oracle_port = os.getenv('ORACLE_PORT', ORACLE_DEFAULT_PORT)
    oracle_service = os.getenv('ORACLE_SERVICE', ORACLE_DEFAULT_SERVICE)

    if not oracle_password:
        return None, 'ORACLE_PASSWORD is not set. Set it in your terminal before starting the app.'

    dsn = f'{oracle_host}:{oracle_port}/{oracle_service}'
    try:
        conn = oracledb.connect(user=oracle_user, password=oracle_password, dsn=dsn)
        return conn, None
    except Exception as exc:
        return None, f'Oracle connection failed: {exc}'


def _build_in_clause(bind_prefix, values, bind_map):
    placeholders = []
    for idx, value in enumerate(values):
        key = f'{bind_prefix}{idx}'
        bind_map[key] = value
        placeholders.append(f':{key}')
    return ', '.join(placeholders)


def _table_columns(cursor, table_name):
    cursor.execute(
        """
        SELECT column_name
        FROM user_tab_columns
        WHERE table_name = :table_name
        ORDER BY column_id
        """,
        {'table_name': table_name},
    )
    return [row[0] for row in cursor.fetchall()]



def _table_columns_info(cursor, table_name):
    cursor.execute(
        """
        SELECT column_name, data_type
        FROM user_tab_columns
        WHERE table_name = :table_name
        ORDER BY column_id
        """,
        {'table_name': table_name},
    )
    return [{'name': row[0], 'type': row[1]} for row in cursor.fetchall()]


def _pick_column(columns, candidates):
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def fetch_table_details(table_name, instance_id=None, patch_no=None, qa_only=False, max_rows=8):
    conn, err = get_oracle_connection()
    if err:
        return {'table': table_name, 'error': err}

    try:
        cur = conn.cursor()
        table_name = table_name.upper()
        column_info = _table_columns_info(cur, table_name)
        if not column_info:
            return {'table': table_name, 'error': f'{table_name} is not accessible in current schema.'}

        columns = [item['name'] for item in column_info]
        where_clauses = []
        bind_map = {}
        warnings = []

        instance_col = _pick_column(columns, ('INSTANCE_ID', 'INSTANCE', 'INST_ID', 'INSTANCE_NAME', 'CLIENT_INSTANCE_ID'))
        patch_col = _pick_column(columns, ('PATCH_NO', 'PATCH_NUMBER', 'PATCH_ID', 'PATCHID'))
        qa_col = _pick_column(columns, ('SERVER_TYPE', 'ENVIRONMENT', 'SERVER_ENV', 'ENV', 'MAP_TYPE', 'SERVER_CATEGORY'))

        if instance_id:
            if instance_col:
                where_clauses.append(f"UPPER(TO_CHAR({instance_col})) = UPPER(:instance_id)")
                bind_map['instance_id'] = instance_id
            else:
                warnings.append('No instance column found; instance filter not applied.')

        if patch_no:
            if patch_col:
                where_clauses.append(f"TO_CHAR({patch_col}) = :patch_no")
                bind_map['patch_no'] = str(patch_no)
            else:
                warnings.append('No patch column found; patch filter not applied.')

        if qa_only:
            if qa_col:
                where_clauses.append(f"UPPER(TO_CHAR({qa_col})) LIKE '%QA%'")
            else:
                warnings.append('No QA classifier column found; QA filter not applied.')

        where_sql = ' WHERE ' + ' AND '.join(where_clauses) if where_clauses else ''

        count_sql = f"SELECT COUNT(1) FROM {table_name}{where_sql}"
        cur.execute(count_sql, bind_map)
        row_count = int(cur.fetchone()[0])

        safe_select_cols = [item['name'] for item in column_info if 'TIME ZONE' not in (item['type'] or '').upper()]
        if not safe_select_cols:
            safe_select_cols = columns
        safe_select_cols = safe_select_cols[:12]

        select_sql = (
            f"SELECT {', '.join(safe_select_cols)} FROM {table_name}{where_sql} FETCH FIRST {int(max_rows)} ROWS ONLY"
        )
        cur.execute(select_sql, bind_map)

        result_rows = []
        for row in cur.fetchall():
            row_map = {}
            for idx, col in enumerate(safe_select_cols):
                value = row[idx]
                row_map[col] = str(value) if value is not None else None
            result_rows.append(row_map)

        return {
            'table': table_name,
            'row_count': row_count,
            'columns': safe_select_cols,
            'rows': result_rows,
            'warnings': warnings,
            'filters': {
                'instance_column': instance_col,
                'patch_column': patch_col,
                'qa_column': qa_col if qa_only else None,
            },
        }
    except Exception as exc:
        return {'table': table_name, 'error': f'Query failed: {exc}'}
    finally:
        conn.close()


def _render_table_excerpt(result, title=None):
    table_name = title or result.get('table', 'TABLE')
    if result.get('error'):
        return [f'{table_name}: {result["error"]}']

    lines = [f'{table_name}: {result["row_count"]} row(s)']
    rows = result.get('rows', [])
    if rows:
        for row in rows[:5]:
            parts = []
            for col, val in row.items():
                if val is None:
                    continue
                parts.append(f'{col}={val}')
                if len(parts) >= 5:
                    break
            lines.append(f"- {'; '.join(parts) if parts else '(all null values)'}")
    else:
        lines.append('- No rows found')

    for warning in result.get('warnings', []):
        lines.append(f'Note: {warning}')
    return lines


def _mask_secret(value):
    if value is None:
        return None
    txt = str(value)
    if not txt:
        return txt
    if len(txt) <= 2:
        return '*' * len(txt)
    return txt[:2] + '*' * (len(txt) - 2)


def fetch_client_server_map_details(instance_id, qa_only=False, max_rows=20):
    conn, err = get_oracle_connection()
    if err:
        return {'table': 'CLIENT_SERVER_MAP', 'error': err}

    try:
        cur = conn.cursor()
        column_info = _table_columns_info(cur, 'CLIENT_SERVER_MAP')
        if not column_info:
            return {'table': 'CLIENT_SERVER_MAP', 'error': 'CLIENT_SERVER_MAP is not accessible in current schema.'}

        columns = [item['name'] for item in column_info]
        where_clauses = []
        bind_map = {}
        warnings = []

        instance_col = _pick_column(columns, ('INSTANCE_ID', 'INSTANCE', 'INST_ID', 'INSTANCE_NAME', 'CLIENT_INSTANCE_ID'))
        qa_col = _pick_column(columns, ('SERVER_TYPE', 'ENVIRONMENT', 'SERVER_ENV', 'ENV', 'MAP_TYPE', 'SERVER_CATEGORY'))

        if instance_col:
            where_clauses.append(f"UPPER(TO_CHAR({instance_col})) = UPPER(:instance_id)")
            bind_map['instance_id'] = instance_id
        else:
            warnings.append('No instance column found in CLIENT_SERVER_MAP; cannot filter by instance reliably.')

        if qa_only and qa_col:
            where_clauses.append(f"UPPER(TO_CHAR({qa_col})) LIKE '%QA%'")
        elif qa_only and not qa_col:
            warnings.append('No QA classifier column found in CLIENT_SERVER_MAP; QA filter not applied.')

        where_sql = ' WHERE ' + ' AND '.join(where_clauses) if where_clauses else ''

        required_cols = ['APP_SOURCE_PATH', 'DB_URL', 'DB_USER_NAME', 'DB_USER_PASSWORD']
        selected_cols = [col for col in required_cols if col in columns]
        missing_cols = [col for col in required_cols if col not in columns]
        for col in missing_cols:
            warnings.append(f'{col} column not found in CLIENT_SERVER_MAP.')

        if not selected_cols:
            return {
                'table': 'CLIENT_SERVER_MAP',
                'row_count': 0,
                'rows': [],
                'warnings': warnings + ['None of required columns are available in CLIENT_SERVER_MAP.'],
            }

        count_sql = f"SELECT COUNT(1) FROM CLIENT_SERVER_MAP{where_sql}"
        cur.execute(count_sql, bind_map)
        row_count = int(cur.fetchone()[0])

        select_sql = (
            f"SELECT {', '.join(selected_cols)} "
            f"FROM CLIENT_SERVER_MAP{where_sql} "
            f"FETCH FIRST {int(max_rows)} ROWS ONLY"
        )
        cur.execute(select_sql, bind_map)

        rows = []
        for raw in cur.fetchall():
            row_map = {}
            for idx, col in enumerate(selected_cols):
                val = raw[idx]
                if col == 'DB_USER_PASSWORD':
                    row_map[col] = _mask_secret(val)
                else:
                    row_map[col] = str(val) if val is not None else None
            rows.append(row_map)

        return {
            'table': 'CLIENT_SERVER_MAP',
            'row_count': row_count,
            'rows': rows,
            'warnings': warnings,
        }
    except Exception as exc:
        return {'table': 'CLIENT_SERVER_MAP', 'error': f'Query failed: {exc}'}
    finally:
        conn.close()


def _render_client_server_map_details(result, title='CLIENT_SERVER_MAP'):
    if result.get('error'):
        return [f'{title}: {result["error"]}']

    lines = [f'{title}: {result.get("row_count", 0)} row(s)']
    rows = result.get('rows', [])
    if not rows:
        lines.append('- No rows found')
    else:
        for row in rows[:10]:
            lines.append(f"- APP_SOURCE_PATH={row.get('APP_SOURCE_PATH')}")
            lines.append(f"  DB_URL={row.get('DB_URL')}")
            lines.append(f"  DB_USER_NAME={row.get('DB_USER_NAME')}")
            lines.append(f"  DB_USER_PASSWORD={row.get('DB_USER_PASSWORD')}")

    for warning in result.get('warnings', []):
        lines.append(f'Note: {warning}')
    lines.append('Note: DB_USER_PASSWORD is masked for security.')
    return lines

def fetch_instance_candidates(search_value):
    conn, err = get_oracle_connection()
    if err:
        return {'error': err}

    try:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                WITH param AS (
                    SELECT :search_val AS val
                    FROM dual
                )
                SELECT instance_id, grp_id, source_schema
                FROM (
                    SELECT CAST(instance_id AS VARCHAR2(200)) AS instance_id,
                           CAST(grp_id AS VARCHAR2(200)) AS grp_id,
                           'NEW SIMS' AS source_schema
                    FROM SIM_ADMIN.INSTANCE_GRP_MAP
                    UNION
                    SELECT CAST(instance_id AS VARCHAR2(200)) AS instance_id,
                           CAST(NULL AS VARCHAR2(200)) AS grp_id,
                           'NEW SIMS' AS source_schema
                    FROM SIM_ADMIN.VERSION_GROUP_MAP
                    UNION
                    SELECT CAST(instance_id AS VARCHAR2(200)) AS instance_id,
                           CAST(NULL AS VARCHAR2(200)) AS grp_id,
                           'OLD SIMS' AS source_schema
                    FROM SIMS_NEW.VERSION_GROUP_MAP
                ) merged_data
                CROSS JOIN param p
                WHERE LOWER(instance_id) LIKE '%' || LOWER(p.val) || '%'
                ORDER BY instance_id
                """,
                {'search_val': search_value},
            )
            rows = cur.fetchall()
        except Exception:
            # Fallback when cross-schema access is restricted.
            cur.execute(
                """
                SELECT DISTINCT INSTANCE_ID
                FROM VERSION_GROUP_MAP
                WHERE LOWER(INSTANCE_ID) LIKE '%' || LOWER(:search_val) || '%'
                ORDER BY INSTANCE_ID
                FETCH FIRST 100 ROWS ONLY
                """,
                {'search_val': search_value},
            )
            rows = [(r[0], None, 'CURRENT_SCHEMA') for r in cur.fetchall()]

        candidates = []
        seen = set()
        for instance_id, grp_id, source_schema in rows:
            if instance_id is None:
                continue
            key = instance_id.upper()
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                'instance_id': instance_id,
                'grp_id': grp_id,
                'source_schema': source_schema,
            })

        return {'search': search_value, 'candidates': candidates}
    except Exception as exc:
        return {'error': f'Query failed: {exc}'}
    finally:
        conn.close()


def resolve_instance_choice(user_message, pending_instances):
    text = (user_message or '').strip()
    if not text or not pending_instances:
        return None

    if text.isdigit():
        idx = int(text)
        if 1 <= idx <= len(pending_instances):
            return pending_instances[idx - 1]

    for instance_id in pending_instances:
        if text.upper() == instance_id.upper():
            return instance_id

    return None


def _render_query_result(title, result, row_limit=20):
    if result.get('error'):
        return [f'{title}: {result["error"]}']

    lines = [f'{title}: {result.get("row_count", 0)} row(s)']
    rows = result.get('rows', [])
    columns = result.get('columns', [])
    if not rows:
        lines.append('- No rows found')
        return lines

    for row in rows[:row_limit]:
        parts = []
        for col in columns[:8]:
            value = row.get(col)
            if value is not None:
                parts.append(f'{col}={value}')
        lines.append(f"- {'; '.join(parts) if parts else '(all null values)'}")

    extra = result.get('row_count', len(rows)) - min(len(rows), row_limit)
    if extra > 0:
        lines.append(f'... {extra} more row(s) not shown')
    return lines


def _query_with_binds(sql, bind_map=None, max_rows=100):
    conn, err = get_oracle_connection()
    if err:
        return {'error': err}

    try:
        cur = conn.cursor()
        cur.execute(sql, bind_map or {})
        columns = [d[0] for d in cur.description] if cur.description else []

        rows = []
        row_count = 0
        for raw in cur:
            row_count += 1
            if len(rows) < max_rows:
                row_map = {}
                for idx, col in enumerate(columns):
                    val = raw[idx]
                    row_map[col] = str(val) if val is not None else None
                rows.append(row_map)

        return {
            'columns': columns,
            'rows': rows,
            'row_count': row_count,
        }
    except Exception as exc:
        return {'error': f'Query failed: {exc}'}
    finally:
        conn.close()


def _normalize_patch_no_text(value):
    text = str(value or '').strip()
    return text


def _build_extra_patch_alert_lines(result, requested_patch_nos):
    rows = result.get('rows', []) if isinstance(result, dict) else []
    requested_set = {_normalize_patch_no_text(p) for p in (requested_patch_nos or []) if _normalize_patch_no_text(p)}
    if not rows or not requested_set:
        return []

    grouped = {}
    for row in rows:
        request_no = _normalize_patch_no_text(row.get('REQUEST_NO'))
        patch_no = _normalize_patch_no_text(row.get('PATCH_NO'))
        client_id = _normalize_patch_no_text(row.get('CLIENT_ID')) or 'UNKNOWN'
        code_desc = _normalize_patch_no_text(row.get('CODE_DESC')) or 'UNKNOWN'
        key = (request_no or 'UNKNOWN', client_id, code_desc)

        bucket = grouped.setdefault(
            key,
            {
                'all': set(),
                'requested': set(),
                'extra': set(),
            },
        )

        if patch_no:
            bucket['all'].add(patch_no)
            if patch_no in requested_set:
                bucket['requested'].add(patch_no)
            else:
                bucket['extra'].add(patch_no)

    alert_lines = []
    for (request_no, client_id, code_desc), bucket in grouped.items():
        if not bucket['extra']:
            continue
        requested_txt = ', '.join(sorted(bucket['requested'])) if bucket['requested'] else ', '.join(sorted(requested_set))
        extra_txt = ', '.join(sorted(bucket['extra']))
        alert_lines.append(
            f"- Request No={request_no}; Client ID={client_id}; Code Desc={code_desc}; Input Patch={requested_txt}; Extra Patch={extra_txt}"
        )

    if not alert_lines:
        return [
            'Extra Patch Check: No extra patches found for the given Patch No, Client ID, and Code Desc.'
        ]

    return ['Extra Patch Check: Additional patch(es) found for the same Request/Client/Code Desc.'] + alert_lines


def fetch_files_by_group_tag(instance_id, group_tag, max_rows=100):
    sql = """
    SELECT *
    FROM SIM_ADMIN.VERSIONHISTORY
    WHERE SERIAL_NO IN (
        SELECT SERIAL_NO
        FROM SIM_ADMIN.VERSION_GROUP_MAP
        WHERE GROUP_ID IN (:group_tag)
    )
      AND INSTANCE_ID = :instance_id
    """
    return _query_with_binds(sql, {'group_tag': group_tag, 'instance_id': instance_id}, max_rows=max_rows)


def fetch_files_by_patch_numbers(instance_id, patch_nos):
    if not patch_nos:
        return {'error': 'Patch number is required.'}

    bind_map = {'instance_id': instance_id}
    in_clause = _build_in_clause('patch', patch_nos, bind_map)
    sql = f"""
    SELECT vh.INSTANCE_ID,
           pd.GROUP_ID,
           pd.PATCH_SEQ_NO,
           pd.PATCH_NO,
           vh.FILENAME,
           vh.PROJECT_PATH,
           pd.PATCH_DESC
    FROM SIM_ADMIN.PATCH_DETAILS pd
    JOIN SIM_ADMIN.VERSION_GROUP_MAP vgm
      ON pd.GROUP_ID = vgm.GROUP_ID
    JOIN SIM_ADMIN.VERSIONHISTORY vh
      ON vgm.SERIAL_NO = vh.SERIAL_NO
    WHERE vh.INSTANCE_ID LIKE '%' || :instance_id || '%'
      AND pd.GROUP_ID LIKE '%' || :instance_id || '%'
      AND TO_CHAR(pd.PATCH_NO) IN ({in_clause})
    ORDER BY pd.PATCH_SEQ_NO DESC
    """
    return _query_with_binds(sql, bind_map)


def fetch_req_patch_stage_details(instance_id, patch_nos, stage_code):
    if not patch_nos:
        return {'error': 'Patch number is required.'}

    bind_map = {
        'instance_id': instance_id,
        'stage_code': stage_code,
    }
    in_clause = _build_in_clause('patch', patch_nos, bind_map)

    sql = f"""
    SELECT pdf.instance_id,
           pi.request_no,
           pi.patch_no,
           cd.code_desc,
           pdf.deploy_status,
           pdf.client_id,
           TO_CHAR(pdf.create_tstamp) AS create_tstamp,
           TO_CHAR(pdf.deploy_possible_tstamp) AS deploy_possible_tstamp,
           TO_CHAR(pdf.deployed_tstamp) AS deployed_tstamp
    FROM patch_deployment_info pdf
    LEFT OUTER JOIN codes_dtl cd
      ON cd.code_id = pdf.stage
     AND cd.code_type = 'PATCH_STATUS'
    JOIN patch_info pi
      ON pdf.request_no = pi.request_no
     AND pdf.instance_id = pi.instance_id
    WHERE pdf.stage = :stage_code
      AND pdf.instance_id = :instance_id
      AND pdf.request_no IN (
            SELECT request_no
            FROM patch_info
            WHERE instance_id = :instance_id
              AND TO_CHAR(patch_no) IN ({in_clause})
      )
    ORDER BY pdf.stage
    """
    return _query_with_binds(sql, bind_map)


def fetch_file_release_patches(instance_id, filename_pattern):
    sql = """
    SELECT DISTINCT vh.serial_no,
           vh.filename,
           vh.project_path,
           vh.revision_no,
           gm.group_id,
           vh.patch_no
    FROM versionhistory vh,
         version_group_map gm
    WHERE vh.serial_no = gm.serial_no
      AND vh.instance_id = gm.instance_id(+)
      AND gm.instance_id IN (:instance_id)
      AND UPPER(vh.filename) LIKE UPPER('%' || :filename_pattern || '%')
    ORDER BY vh.serial_no DESC
    """
    return _query_with_binds(sql, {'instance_id': instance_id, 'filename_pattern': filename_pattern})


def fetch_group_tag_owner(group_tag):
    sql = """
    SELECT user_name
    FROM user_profile
    WHERE user_id IN (
        SELECT modify_user
        FROM sim_admin.version_group_map
        WHERE group_id = :group_tag
    )
    """
    return _query_with_binds(sql, {'group_tag': group_tag})


def fetch_user_display_name(username):
    """Looks up USER_NAME in USER_PROFILE for `username` (case-insensitive),
    used to show a friendlier "Logged in as ..." name. Returns None if no
    matching row is found or Oracle is unreachable (caller falls back to the
    login username itself)."""
    conn, err = get_oracle_connection()
    if err:
        logger.warning('fetch_user_display_name could not connect for %s: %s', username, err)
        return None
    try:
        cur = conn.cursor()
        cur.execute('SELECT user_name FROM user_profile WHERE UPPER(user_id) = UPPER(:username)', {'username': username})
        row = cur.fetchone()
        return row[0] if row and row[0] else None
    except Exception as exc:
        logger.warning('fetch_user_display_name failed for %s: %s', username, exc)
        return None
    finally:
        conn.close()


def fetch_stage_reference():
    sql = """
    SELECT code_id, code_desc
    FROM codes_dtl
    WHERE code_type = 'PATCH_STATUS'
    ORDER BY TO_NUMBER(code_id)
    """
    return _query_with_binds(sql, max_rows=500)


def fetch_test_deployment_pending():
    sql = """
    SELECT DISTINCT TO_CHAR(TRUNC(G.CREATE_TSTAMP), 'YYYY-MM-DD') AS DATE_VALUE,
           G.INSTANCE_ID,
           G.GROUP_ID,
           DEV.USER_NAME AS GROUPED_BY,
           R.STATUS,
           R.VERSION_TAKEN_BY
    FROM VERSION_GROUP_MAP G
    LEFT OUTER JOIN (
        SELECT B.GROUP_TAG,
               CD.CODE_DESC AS STATUS,
               VC.USER_NAME AS VERSION_TAKEN_BY
        FROM BUILD_DTL B,
             (SELECT GROUP_TAG, MAX(BUILD_SEQ_NO) AS BUILD_NO
              FROM BUILD_DTL
              WHERE GROUP_TAG IN (
                  SELECT DISTINCT GROUP_ID
                  FROM VERSION_GROUP_MAP VGM
                  WHERE DEPLOYED_STATUS='N'
                    AND INSTANCE_ID NOT IN ('IRS_DEV','IRS','DOMS_NEW','SONY2-OPM')
                    AND TRUNC(CREATE_TSTAMP)>=TRUNC(SYSDATE)-30
              )
              GROUP BY GROUP_TAG) D,
             USER_PROFILE VC,
             CODES_DTL CD
        WHERE B.GROUP_TAG = D.GROUP_TAG
          AND B.BUILD_SEQ_NO = D.BUILD_NO
          AND B.CREATE_USER = VC.USER_ID
          AND CD.CODE_TYPE='BD_STATUS_CD'
          AND B.STATUS_CD=CD.CODE_ID
    ) R
      ON G.GROUP_ID = R.GROUP_TAG,
      USER_PROFILE DEV
    WHERE G.DEPLOYED_STATUS='N'
      AND G.CREATE_USER = DEV.USER_ID
      AND G.INSTANCE_ID NOT IN ('S7TEC','BROOKS6P2379','S73DIB','S71SONYAUS','CASEYSWCS','S73ALND','S7FB','UPSHC','DOMSUMG71P210','S241P40CH','S241P61ACT','NFIS6P2566','UPSPDZNP5994','S73TEKTON','SCFLEX242','SCP7_CORE','SCP7_BASE','DOMS241','S232P16SOS','S74MP112JAS','S241P12ITS','S242P6SFB','S242DSW','SCFLEX241')
      AND TRUNC(G.CREATE_TSTAMP)>=(SYSDATE)-150
    ORDER BY DATE_VALUE DESC
    """
    return _query_with_binds(sql, max_rows=500)


def fetch_auto_patch_extracted():
    sql = """
    SELECT DISTINCT TO_CHAR(p.create_tstamp, 'YYYY-MM-DD') AS patch_date,
           p.instance_id,
           p.group_id,
           p.patch_seq_no,
           p.patch_no,
           p.patch_after_stamp_no,
           p.build_by,
           p.patch_status
    FROM patch_details p
    WHERE p.patch_status = 'EXTRACTED'
      AND TRUNC(p.modify_tstamp) >= (SYSDATE) - 30
    ORDER BY p.patch_seq_no DESC
    """
    return _query_with_binds(sql, max_rows=500)



def _empty_query_result(columns=None):
    return {'columns': columns or [], 'rows': [], 'row_count': 0}


def fetch_patch_seq_flow(patch_seq_no):
    instance_from_seq, patch_type, seq_part = parse_patch_seq_parts(patch_seq_no)

    patch_details_sql = """
    SELECT INSTANCE_ID, GROUP_ID, PATCH_NO, PATCH_SEQ_NO
    FROM PATCH_DETAILS
    WHERE PATCH_SEQ_NO = :patch_seq_no
    """
    patch_details_result = _query_with_binds(
        patch_details_sql,
        {'patch_seq_no': patch_seq_no},
        max_rows=200,
    )
    if patch_details_result.get('error'):
        return {'error': patch_details_result['error']}

    rows = patch_details_result.get('rows', [])
    instance_id = None
    for row in rows:
        if row.get('INSTANCE_ID'):
            instance_id = row.get('INSTANCE_ID')
            break
    if not instance_id:
        instance_id = instance_from_seq

    group_ids = sorted({row.get('GROUP_ID') for row in rows if row.get('GROUP_ID')})

    patch_no_set = set()
    for row in rows:
        raw_patch_no = row.get('PATCH_NO')
        if raw_patch_no is None:
            continue
        normalized_patch_no = str(raw_patch_no).strip()
        if not normalized_patch_no:
            continue
        if normalized_patch_no.upper() in ('NULL', 'N/A', '-'):
            continue
        patch_no_set.add(normalized_patch_no)
    patch_nos = sorted(patch_no_set)
    versionhistory_result = _empty_query_result()
    if instance_id and group_ids:
        bind_map = {'instance_id': instance_id}
        in_clause = _build_in_clause('gid', group_ids, bind_map)
        versionhistory_sql = f"""
        SELECT *
        FROM VERSIONHISTORY
        WHERE SERIAL_NO IN (
            SELECT SERIAL_NO
            FROM VERSION_GROUP_MAP
            WHERE GROUP_ID IN ({in_clause})
        )
          AND INSTANCE_ID = :instance_id
        """
        versionhistory_result = _query_with_binds(versionhistory_sql, bind_map, max_rows=300)

    patch_build_log_result = _empty_query_result(['PATCH_EXTRACT_FLAG', 'BUILD_START'])
    if not patch_nos:
        build_log_sql = """
        SELECT PATCH_EXTRACT_FLAG, BUILD_START
        FROM PATCH_BUILD_LOG
        WHERE PATCH_SEQ_NO = :patch_seq_no
        """
        patch_build_log_result = _query_with_binds(
            build_log_sql,
            {'patch_seq_no': patch_seq_no},
            max_rows=50,
        )

    return {
        'patch_seq_no': patch_seq_no,
        'instance_id': instance_id,
        'instance_from_seq': instance_from_seq,
        'patch_type': patch_type,
        'seq_part': seq_part,
        'group_ids': group_ids,
        'patch_nos': patch_nos,
        'patch_details': patch_details_result,
        'versionhistory': versionhistory_result,
        'patch_build_log': patch_build_log_result,
    }

def fetch_group_tag_for_patch_seq(patch_seq_no):
    conn, err = get_oracle_connection()
    if err:
        return {'error': err}

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT GROUP_ID
            FROM PATCH_DETAILS
            WHERE PATCH_SEQ_NO = :patch_seq_no
              AND GROUP_ID IS NOT NULL
            """,
            {'patch_seq_no': patch_seq_no},
        )
        group_ids = [row[0] for row in cur.fetchall() if row[0]]
        return {'patch_seq_no': patch_seq_no, 'group_ids': group_ids}
    except Exception as exc:
        return {'error': f'Query failed: {exc}'}
    finally:
        conn.close()


def fetch_group_tag_for_patch_no(instance_id, patch_no):
    conn, err = get_oracle_connection()
    if err:
        return {'error': err}

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT GROUP_ID
            FROM PATCH_DETAILS
            WHERE INSTANCE_ID = :instance_id
              AND TO_CHAR(PATCH_NO) = :patch_no
              AND GROUP_ID IS NOT NULL
            """,
            {'instance_id': instance_id, 'patch_no': patch_no},
        )
        group_ids = [row[0] for row in cur.fetchall() if row[0]]
        return {'patch_no': patch_no, 'group_ids': group_ids}
    except Exception as exc:
        return {'error': f'Query failed: {exc}'}
    finally:
        conn.close()


def fetch_group_tag_deployment_details(group_tag):
    conn, err = get_oracle_connection()
    if err:
        return {'error': err}

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT INSTANCE_ID, COUNT(SERIAL_NO)
            FROM VERSION_GROUP_MAP
            WHERE GROUP_ID = :group_tag
            GROUP BY INSTANCE_ID
            """,
            {'group_tag': group_tag},
        )
        vgm_rows = cur.fetchall()
        instance_id = vgm_rows[0][0] if vgm_rows else None
        file_count = sum(row[1] for row in vgm_rows) if vgm_rows else 0

        cur.execute(
            """
            SELECT CD.CODE_DESC AS DEPLOY_STATUS,
                   VC.USER_NAME AS DEPLOYED_BY
            FROM BUILD_DTL B,
                 (SELECT GROUP_TAG, MAX(BUILD_SEQ_NO) AS BUILD_NO
                  FROM BUILD_DTL
                  WHERE GROUP_TAG = :group_tag
                  GROUP BY GROUP_TAG) D,
                 USER_PROFILE VC,
                 CODES_DTL CD
            WHERE B.GROUP_TAG = D.GROUP_TAG
              AND B.BUILD_SEQ_NO = D.BUILD_NO
              AND B.CREATE_USER = VC.USER_ID
              AND CD.CODE_TYPE = 'BD_STATUS_CD'
              AND B.STATUS_CD = CD.CODE_ID
            """,
            {'group_tag': group_tag},
        )
        status_row = cur.fetchone()
        deploy_status = status_row[0] if status_row else None
        deployed_by = status_row[1] if status_row else None

        cur.execute(
            """
            SELECT DISTINCT TO_CHAR(PATCH_NO)
            FROM PATCH_DETAILS
            WHERE GROUP_ID = :group_tag
              AND PATCH_NO IS NOT NULL
            ORDER BY 1
            """,
            {'group_tag': group_tag},
        )
        patch_numbers = [row[0] for row in cur.fetchall() if row[0] is not None]

        if instance_id is None and deploy_status is None and deployed_by is None and not patch_numbers and file_count == 0:
            return {'group_tag': group_tag, 'not_found': True}

        return {
            'group_tag': group_tag,
            'instance_id': instance_id,
            'file_count': file_count,
            'deploy_status': deploy_status,
            'deployed_by': deployed_by,
            'patch_numbers': patch_numbers,
        }
    except Exception as exc:
        return {'error': f'Query failed: {exc}'}
    finally:
        conn.close()


def fetch_group_tags_deployment_summary(group_tags):
    """Combined deployment summary across multiple group tags (e.g. all tags for one patch seq no)."""
    if not group_tags:
        return {'not_found': True}

    conn, err = get_oracle_connection()
    if err:
        return {'error': err}

    try:
        cur = conn.cursor()

        bind_map = {}
        in_clause = _build_in_clause('gtag', group_tags, bind_map)
        cur.execute(
            f"""
            SELECT COUNT(DISTINCT SERIAL_NO)
            FROM VERSION_GROUP_MAP
            WHERE GROUP_ID IN ({in_clause})
            """,
            bind_map,
        )
        row = cur.fetchone()
        file_count = int(row[0]) if row and row[0] is not None else 0

        bind_map = {}
        in_clause = _build_in_clause('gtag', group_tags, bind_map)
        cur.execute(
            f"""
            SELECT CD.CODE_DESC AS DEPLOY_STATUS,
                   VC.USER_NAME AS DEPLOYED_BY
            FROM BUILD_DTL B,
                 (SELECT GROUP_TAG, MAX(BUILD_SEQ_NO) AS BUILD_NO
                  FROM BUILD_DTL
                  WHERE GROUP_TAG IN ({in_clause})
                  GROUP BY GROUP_TAG) D,
                 USER_PROFILE VC,
                 CODES_DTL CD
            WHERE B.GROUP_TAG = D.GROUP_TAG
              AND B.BUILD_SEQ_NO = D.BUILD_NO
              AND B.CREATE_USER = VC.USER_ID
              AND CD.CODE_TYPE = 'BD_STATUS_CD'
              AND B.STATUS_CD = CD.CODE_ID
            """,
            bind_map,
        )
        status_rows = cur.fetchall()
        deploy_statuses = sorted({r[0] for r in status_rows if r[0] is not None})
        deployed_by_list = sorted({r[1] for r in status_rows if r[1] is not None})

        bind_map = {}
        in_clause = _build_in_clause('gtag', group_tags, bind_map)
        cur.execute(
            f"""
            SELECT DISTINCT TO_CHAR(PATCH_NO)
            FROM PATCH_DETAILS
            WHERE GROUP_ID IN ({in_clause})
              AND PATCH_NO IS NOT NULL
            ORDER BY 1
            """,
            bind_map,
        )
        patch_numbers = [r[0] for r in cur.fetchall() if r[0] is not None]

        if file_count == 0 and not deploy_statuses and not deployed_by_list and not patch_numbers:
            return {'group_tags': group_tags, 'not_found': True}

        return {
            'group_tags': group_tags,
            'file_count': file_count,
            'deploy_status': deploy_statuses,
            'deployed_by': deployed_by_list,
            'patch_numbers': patch_numbers,
        }
    except Exception as exc:
        return {'error': f'Query failed: {exc}'}
    finally:
        conn.close()


def _resolve_stage_display(stage_code):
    if not stage_code:
        return 'UNKNOWN'
    for name, code in STAGE_NAME_TO_CODE.items():
        if code == stage_code:
            return name
    return stage_code


def _normalize_status_value(status):
    value = str(status or '').strip()
    return value if value else 'UNKNOWN'


def fetch_patch_numbers_for_instance(instance_id):
    conn, err = get_oracle_connection()
    if err:
        return {'error': err}

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT TO_CHAR(PATCH_NO) AS PATCH_NO
            FROM PATCH_DETAILS
            WHERE INSTANCE_ID = :instance_id
            ORDER BY PATCH_NO DESC
            FETCH FIRST 100 ROWS ONLY
            """,
            {'instance_id': instance_id},
        )
        patch_nos = [row[0] for row in cur.fetchall() if row[0] is not None]
        return {'instance_id': instance_id, 'patch_nos': patch_nos}
    except Exception as exc:
        return {'error': f'Query failed: {exc}'}
    finally:
        conn.close()


def fetch_group_tags_for_instance(instance_id):
    conn, err = get_oracle_connection()
    if err:
        return {'error': err}

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT GROUP_ID
            FROM VERSION_GROUP_MAP
            WHERE INSTANCE_ID = :instance_id
              AND GROUP_ID IS NOT NULL
            ORDER BY GROUP_ID DESC
            FETCH FIRST 100 ROWS ONLY
            """,
            {'instance_id': instance_id},
        )
        group_ids = [row[0] for row in cur.fetchall() if row[0] is not None]
        return {'instance_id': instance_id, 'group_ids': group_ids}
    except Exception as exc:
        return {'error': f'Query failed: {exc}'}
    finally:
        conn.close()


def fetch_patch_owner(instance_id, patch_no):
    conn, err = get_oracle_connection()
    if err:
        return {'error': err}

    try:
        cur = conn.cursor()
        patch_cols = _table_columns(cur, 'PATCH_DETAILS')
        user_cols = _table_columns(cur, 'USER_PROFILE')

        if not patch_cols:
            return {'error': 'PATCH_DETAILS is not accessible in current schema.'}
        if not user_cols:
            return {'error': 'USER_PROFILE is not accessible in current schema.'}

        build_candidates = [
            col for col in patch_cols
            if col in ('BUILD_BY', 'BUILT_BY', 'BUILD_USER', 'BUILD_USER_ID')
        ]
        if not build_candidates:
            build_candidates = [col for col in patch_cols if 'BUILD' in col]
        if not build_candidates:
            return {'error': 'No BUILD BY style column found in PATCH_DETAILS.'}

        build_col = build_candidates[0]

        cur.execute(
            f"""
            SELECT {build_col}
            FROM PATCH_DETAILS
            WHERE INSTANCE_ID = :instance_id
              AND TO_CHAR(PATCH_NO) = :patch_no
            """,
            {'instance_id': instance_id, 'patch_no': patch_no},
        )
        builder_ids = sorted({row[0] for row in cur.fetchall() if row[0] is not None})

        if not builder_ids:
            return {
                'instance_id': instance_id,
                'patch_no': patch_no,
                'build_column': build_col,
                'builder_ids': [],
                'builder_names': {},
            }

        if 'USER_ID' not in user_cols or 'USER_NAME' not in user_cols:
            return {
                'instance_id': instance_id,
                'patch_no': patch_no,
                'build_column': build_col,
                'builder_ids': builder_ids,
                'builder_names': {},
                'warning': 'USER_PROFILE is missing USER_ID or USER_NAME columns.',
            }

        bind_map = {}
        in_clause = _build_in_clause('uid', builder_ids, bind_map)
        cur.execute(
            f"""
            SELECT USER_ID, USER_NAME
            FROM USER_PROFILE
            WHERE USER_ID IN ({in_clause})
            """,
            bind_map,
        )
        builder_names = {row[0]: row[1] for row in cur.fetchall()}

        return {
            'instance_id': instance_id,
            'patch_no': patch_no,
            'build_column': build_col,
            'builder_ids': builder_ids,
            'builder_names': builder_names,
        }
    except Exception as exc:
        return {'error': f'Query failed: {exc}'}
    finally:
        conn.close()


def fetch_patch_details(instance_id, patch_no):
    conn, err = get_oracle_connection()
    if err:
        return {'error': err}

    try:
        cur = conn.cursor()

        # Keep file-list flow minimal to avoid timezone conversion issues in thin mode.
        cur.execute(
            """
            SELECT PATCH_NO, GROUP_ID
            FROM PATCH_DETAILS
            WHERE TO_CHAR(PATCH_NO) = :patch_no
              AND INSTANCE_ID = :instance_id
            """,
            {'patch_no': patch_no, 'instance_id': instance_id},
        )
        patch_rows = cur.fetchall()
        group_ids = sorted({row[1] for row in patch_rows if row[1] is not None})

        serial_nos = []
        if group_ids:
            bind_map = {'instance_id': instance_id}
            in_clause = _build_in_clause('gid', group_ids, bind_map)
            cur.execute(
                f"""
                SELECT SERIAL_NO
                FROM VERSION_GROUP_MAP
                WHERE INSTANCE_ID = :instance_id
                  AND GROUP_ID IN ({in_clause})
                """,
                bind_map,
            )
            serial_nos = sorted({row[0] for row in cur.fetchall() if row[0] is not None})

        filenames = []
        if serial_nos:
            bind_map = {'instance_id': instance_id}
            in_clause = _build_in_clause('sid', serial_nos, bind_map)
            cur.execute(
                f"""
                SELECT FILENAME
                FROM VERSIONHISTORY
                WHERE INSTANCE_ID = :instance_id
                  AND SERIAL_NO IN ({in_clause})
                ORDER BY FILENAME
                """,
                bind_map,
            )
            filenames = [row[0] for row in cur.fetchall() if row[0]]

        return {
            'instance_id': instance_id,
            'patch_no': patch_no,
            'group_ids': group_ids,
            'serial_nos': serial_nos,
            'filenames': filenames,
            'unique_filenames': sorted(set(filenames)),
        }
    except Exception as exc:
        return {'error': f'Query failed: {exc}'}
    finally:
        conn.close()


def fetch_instance_ids_for_report():
    conn, err = get_oracle_connection()
    if err:
        return {'error': err}

    try:
        cur = conn.cursor()
        cur.execute('SELECT DISTINCT INSTANCE_ID FROM INSTANCE_GROUP_MAP ORDER BY INSTANCE_ID')
        instance_ids = [row[0] for row in cur.fetchall() if row[0]]
        return {'instance_ids': instance_ids}
    except Exception as exc:
        return {'error': f'Query failed: {exc}'}
    finally:
        conn.close()


def fetch_active_instance_ids():
    """Instance ID source for the Instance Details tab's dropdown."""
    conn, err = get_oracle_connection()
    if err:
        return {'error': err}

    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT INSTANCE_ID FROM INSTANCE_HDR WHERE ACTIVE_FLAG = 'Y' ORDER BY INSTANCE_ID")
        instance_ids = [row[0] for row in cur.fetchall() if row[0]]
        return {'instance_ids': instance_ids}
    except Exception as exc:
        return {'error': f'Query failed: {exc}'}
    finally:
        conn.close()


def fetch_test_status_values():
    conn, err = get_oracle_connection()
    if err:
        return {'error': err}

    try:
        cur = conn.cursor()
        cur.execute("SELECT CODE_DESC FROM CODES_DTL WHERE CODE_TYPE LIKE '%TEST_STATUS%' ORDER BY CODE_DESC")
        test_statuses = [row[0] for row in cur.fetchall() if row[0]]
        return {'test_statuses': test_statuses}
    except Exception as exc:
        return {'error': f'Query failed: {exc}'}
    finally:
        conn.close()


def fetch_test_status_report(instance_ids, start_date, end_date, test_statuses=None):
    conn, err = get_oracle_connection()
    if err:
        return {'error': err}

    try:
        cur = conn.cursor()
        bind_map = {'start_date': start_date, 'end_date': end_date}

        instance_filter = ''
        if instance_ids:
            in_clause = _build_in_clause('inst', instance_ids, bind_map)
            instance_filter = f'AND vgm.instance_id IN ({in_clause})'

        status_filter = ''
        if test_statuses:
            status_clause = _build_in_clause('status', test_statuses, bind_map)
            status_filter = f'AND cd2.code_desc IN ({status_clause})'

        sql = f"""
        SELECT DISTINCT
            TO_CHAR(TRUNC(vgm.CREATE_TSTAMP), 'YYYY-MM-DD')     AS GROUP_DATE,
            vgm.instance_id                                      AS INSTANCE_ID,
            vgm.GROUP_ID                                         AS GROUP_ID,
            vgm.GROUP_DESC                                       AS GROUP_DESC,
            cd.code_desc                                         AS CHANGE_IMPACT,
            cd1.code_desc                                        AS PRIORITY,
            vgm.client_id                                        AS CLIENT_ID,
            vgm.TICKET_ID                                        AS TICKET_ID,
            vgm.TASK_ID                                          AS TASK_ID,
            vgm.CHANGE_HISTORY                                   AS CHANGE_HISTORY,
            up1.user_name                                        AS CREATED_BY,
            up.user_name                                         AS TEST_BY,
            cd2.code_desc                                        AS TEST_STATUS,
            NVL(TO_CHAR(vgs.QA_TARGET_DATE, 'DD-MON-YYYY'), ' ') AS QA_TARGET_DATE,
            NVL(TO_CHAR(vgs.test_tstamp, 'DD-MON-YYYY'), ' ')    AS QA_TESTED_DATE,
            vgs.TEST_COMMENTS                                    AS TEST_COMMENTS,
            vgs.vc_comments                                      AS VC_COMMENTS,
            vgs.VC_PATCH_NO                                      AS PATCH_NO,
            vgs.VC_PATCH_AFTER_STAMP_NO                          AS PATCH_AFTER_STAMP_NO,
            vgs.VC_VERSION_STAMP_NO                              AS STAMPED_VERSION_NO,
            vgm.help_doc_req                                     AS HELP_DOC_REQ
        FROM VERSION_GROUP_MAP vgm,
             VERSION_GROUP_STATUS vgs,
             USER_PROFILE up,
             USER_PROFILE up1,
             CODES_DTL cd,
             CODES_DTL cd1,
             CODES_DTL cd2
        WHERE vgm.instance_id = vgs.instance_id
          AND vgm.GROUP_ID = vgs.GROUP_ID
          AND cd.code_type = 'CHANGE_IMPACT'
          AND vgm.change_impact = cd.code_id
          AND cd1.code_type = 'PRIORITY'
          AND vgm.PRIORITY = cd1.code_id
          AND cd2.code_type = 'TEST_STATUS'
          AND vgs.test_status = cd2.code_id
          AND vgs.TEST_BY = up.user_id(+)
          AND vgm.CREATE_USER = up1.user_id
          {instance_filter}
          {status_filter}
          AND TRUNC(vgm.CREATE_TSTAMP) >= TO_DATE(:start_date, 'YYYY-MM-DD')
          AND TRUNC(vgm.CREATE_TSTAMP) <= TO_DATE(:end_date, 'YYYY-MM-DD')
        ORDER BY GROUP_DATE, INSTANCE_ID, GROUP_ID
        """
        cur.execute(sql, bind_map)
        columns = [d[0] for d in cur.description] if cur.description else []

        rows = []
        for raw in cur:
            row_map = {}
            for idx, col in enumerate(columns):
                value = raw[idx]
                row_map[col] = str(value) if value is not None else None
            rows.append(row_map)

        return {'columns': columns, 'rows': rows, 'row_count': len(rows)}
    except Exception as exc:
        return {'error': f'Query failed: {exc}'}
    finally:
        conn.close()


# Instance IDs available in the Product YTT WIP report's Instance ID dropdown.
PRODUCT_YTT_WIP_INSTANCE_IDS = [
    'SCFLEX261', 'S71FB', 'S72P470LOT', 'S73FB', 'S73MAIN', 'S73PFB',
    'S74MAIN', 'S7CRWIZARD', 'S7FB', 'S7SCUB', 'S7SPLCHDD',
    'SCFLEX232', 'SCFLEX241', 'SCFLEX242', 'SCFLEX251', 'SCFLEX252',
    'SCP6_1', 'SCP6_BASE', 'SCP6_CORE', 'SCP6_PORTAL62',
    'SCP70', 'SCP71', 'SCP72', 'SCP7DBCR', 'SCP7DBCR_B',
    'SCP7DOC', 'SCP7MDMDBCR', 'SCP7_BASE', 'SCP7_BASE_B',
    'SCP7_CORE', 'SCP7_CORE_B', 'SWPPRODUCT', 'UPSHC', 'WMSCLOUD5',
]


def fetch_product_ytt_wip_instance_ids():
    conn, err = get_oracle_connection()
    if err:
        return {'error': err}

    try:
        cur = conn.cursor()
        bind_map = {}
        in_clause = _build_in_clause('inst', PRODUCT_YTT_WIP_INSTANCE_IDS, bind_map)
        cur.execute(
            f"""
            SELECT DISTINCT instance_id
            FROM VERSION_GROUP_MAP
            WHERE instance_id IN ({in_clause}) OR instance_id LIKE 'SCFLEX2%'
            ORDER BY instance_id
            """,
            bind_map,
        )
        instance_ids = [row[0] for row in cur.fetchall() if row[0]]
        return {'instance_ids': instance_ids}
    except Exception as exc:
        return {'error': f'Query failed: {exc}'}
    finally:
        conn.close()


def fetch_product_ytt_wip(start_date, end_date, instance_ids=None):
    conn, err = get_oracle_connection()
    if err:
        return {'error': err}

    try:
        cur = conn.cursor()
        bind_map = {'start_date': start_date, 'end_date': end_date}

        if instance_ids:
            in_clause = _build_in_clause('selinst', instance_ids, bind_map)
            instance_filter = f'vgm.instance_id IN ({in_clause})'
        else:
            in_clause = _build_in_clause('inst', PRODUCT_YTT_WIP_INSTANCE_IDS, bind_map)
            instance_filter = f"(vgm.instance_id IN ({in_clause}) OR vgm.instance_id LIKE 'SCFLEX2%')"

        sql = f"""
        SELECT DISTINCT
               TO_CHAR(vgm.CREATE_TSTAMP, 'DD-MON-YYYY') GROUP_DATE,
               vgm.instance_id,
               vgm.GROUP_ID,
               vgm.GROUP_NAME,
               vgm.ticket_id AS TICKET_ID,
               cd.code_desc AS CHANGE_IMPACT,
               cd1.code_desc AS PRIORITY,
               vgm.CHANGE_HISTORY,
               up1.user_name AS CREATED_BY,
               up.user_name AS TEST_BY,
               cd2.code_desc AS TEST_STATUS,
               NVL(TO_CHAR(VGS.QA_TARGET_DATE,'DD-MON-YYYY'),' ') AS QA_TARGET_DATE,
               vgs.TEST_COMMENTS,
               vgm.CREATE_TSTAMP
        FROM VERSION_GROUP_MAP vgm,
             VERSION_GROUP_STATUS vgs,
             USER_PROFILE up,
             USER_PROFILE up1,
             CODES_DTL cd,
             CODES_DTL cd1,
             CODES_DTL cd2
        WHERE vgm.instance_id = vgs.instance_id
          AND vgm.GROUP_ID = vgs.GROUP_ID
          AND cd.code_type = 'CHANGE_IMPACT'
          AND vgm.change_impact = cd.code_id
          AND cd1.code_type = 'PRIORITY'
          AND vgm.PRIORITY = cd1.code_id
          AND cd2.code_type = 'TEST_STATUS'
          AND vgs.test_status = cd2.code_id
          AND vgs.TEST_BY = up.user_id(+)
          AND vgm.CREATE_USER = up1.user_id
          AND {instance_filter}
          AND vgs.TEST_STATUS IN ('', '10', '20')
          AND TRUNC(vgs.CREATE_TSTAMP) >= TO_DATE(:start_date, 'YYYY-MM-DD')
          AND TRUNC(vgs.CREATE_TSTAMP) <= TO_DATE(:end_date, 'YYYY-MM-DD')
        ORDER BY vgm.CREATE_TSTAMP
        """
        cur.execute(sql, bind_map)
        columns = [d[0] for d in cur.description] if cur.description else []

        rows = []
        for raw in cur:
            row_map = {}
            for idx, col in enumerate(columns):
                value = raw[idx]
                row_map[col] = str(value) if value is not None else None
            rows.append(row_map)

        logger.info('Product YTT WIP report generated: %d row(s).', len(rows))
        return {'columns': columns, 'rows': rows, 'row_count': len(rows)}
    except Exception as exc:
        logger.exception('Product YTT WIP report query failed.')
        return {'error': f'Query failed: {exc}'}
    finally:
        conn.close()


# INSTANCE_HDR exclusions shared by both schemas behind the IMPL YTT WIP report.
_IMPL_YTT_WIP_EXCLUDED_INSTANCE_IDS = [
    'AD', 'BASE', 'BEVWMS', 'BILLINGENGINE', 'CASEYSWCS', 'D71FB', 'DEMO536',
    'DOMS_PHASE2', 'DOMS241', 'DOMS242', 'DOMS251', 'DOMS537', 'DOMS7',
    'DOMSHC', 'DOMSHONP132', 'DOMSOPT480P595', 'DOMSOPTUMP480', 'DOMSPRODUCT',
    'DOMSSMBP192', 'DOMSUMG71P210', 'DOMSWTGP149', 'ELITE', 'FAP', 'IBS',
    'IBS_CRBROWSER', 'IBS_DBLISTENER', 'IBS_JQUERY', 'IBS_MIGRATION',
    'IBS_SLICKGRID', 'IBSWEBSERVICE', 'IRSCAMPUS241', 'IRSDI', 'IRSDI241',
    'IRSELITE_JBOSS', 'IRSELITE241', 'IRSSSO241', 'JASCONFIG', 'MARKEN',
    'OPTS6P2415P1151', 'P2F', 'P2F73', 'P2F73P383OPTUM', 'P2FCASEYSP1443',
    'P2FDEMO', 'P2FINCOMVMI', 'P2FPROD', 'P2FPRODUCT', 'P2FUMGP1443',
    'RASMIGRATION', 'RELIABLE', 'S242ITSAPI', 'S242P175ACTAPI',
    'S251P112CONLMS', 'S251P21VANLMS', 'S71FB', 'S72P470LOT', 'S73FB',
    'S73P625P301DD', 'S73P960FL', 'S73PFB', 'S7CRWIZARD', 'S7FB', 'S7SCUB',
    'S7SPLCHDD', 'SCEP_BASE', 'SCEP_CORE', 'SCFLEX242', 'SCFLEX251',
    'SCFLEX252', 'SCFLEXV25P160', 'SCFLEXV25P254', 'SCP6_1', 'SCP6_BASE',
    'SCP6_CORE', 'SCP6_PORTAL62', 'SCP6_SFTBI', 'SCP7_BASE', 'SCP7_BASE_B',
    'SCP7_CORE', 'SCP7_CORE_B', 'SCP70', 'SCP71', 'SCP72', 'SCP7DBCR',
    'SCP7DBCR_B', 'SCP7DOC', 'SCP7MDMDBCR', 'SEARSCLOUD', 'SEARSDOMSCLOUD',
    'SEARSINHCLOUD', 'SEARSWSCLOUD', 'SIMS', 'SONY_OPM', 'SWP242',
    'SWP242ITS', 'SWP242P3CRANE', 'SWP242P8ACT', 'SWP242P8AD', 'SWP242P9TIG',
    'SWP251', 'SWP251P10DSW', 'SWP252', 'SWPPRODUCT', 'TRANSUK', 'TRANSUMG',
    'UMGVENDORPORTAL', 'UMGVWH', 'UPSHC', 'UPSUIP607QTC', 'VENDORPORTAL',
    'VPDULUTHP19', 'VWSOURCE', 'WEBPORTALP85SOS', 'WMSCLOUD5', 'SWPPRODUCT',
    'D261P258KP', 'D261P258STFIX',
]
# SIMS_NEW additionally excludes ALLHEARTS and allows a handful of SCFLEX* ids through.
_IMPL_YTT_WIP_EXCLUDED_INSTANCE_IDS_SIMS_NEW = _IMPL_YTT_WIP_EXCLUDED_INSTANCE_IDS + ['ALLHEARTS']
_IMPL_YTT_WIP_SCFLEX_ALLOWED_SIMS_NEW = ['SCFLEXV25P254', 'SCFLEXV25P888', 'SCFLEX5V52P14', 'SCFLEXV25P1151']


def _impl_ytt_wip_instance_hdr_where(schema, bind_map, bind_prefix):
    if schema == 'SIM_ADMIN':
        excl_clause = _build_in_clause(bind_prefix, _IMPL_YTT_WIP_EXCLUDED_INSTANCE_IDS, bind_map)
        return f"""active_flag = 'Y'
              AND instance_id NOT LIKE 'SCFLEX%'
              AND instance_id NOT LIKE '%MAIN'
              AND instance_id NOT IN ({excl_clause})"""

    excl_clause = _build_in_clause(bind_prefix, _IMPL_YTT_WIP_EXCLUDED_INSTANCE_IDS_SIMS_NEW, bind_map)
    allowed_clause = _build_in_clause(f'{bind_prefix}scf', _IMPL_YTT_WIP_SCFLEX_ALLOWED_SIMS_NEW, bind_map)
    return f"""active_flag = 'Y'
          AND (instance_id NOT LIKE 'SCFLEX%' OR instance_id IN ({allowed_clause}))
          AND instance_id NOT LIKE '%MAIN'
          AND instance_id NOT IN ({excl_clause})"""


def fetch_impl_ytt_wip_instance_ids():
    conn, err = get_oracle_connection()
    if err:
        return {'error': err}

    try:
        cur = conn.cursor()
        bind_map = {}
        where_sim_admin = _impl_ytt_wip_instance_hdr_where('SIM_ADMIN', bind_map, 'exclA')
        where_sims_new = _impl_ytt_wip_instance_hdr_where('SIMS_NEW', bind_map, 'exclB')
        sql = f"""
        SELECT DISTINCT instance_id FROM (
            SELECT instance_id FROM SIM_ADMIN.INSTANCE_HDR WHERE {where_sim_admin}
            UNION
            SELECT instance_id FROM SIMS_NEW.INSTANCE_HDR WHERE {where_sims_new}
        )
        ORDER BY instance_id
        """
        cur.execute(sql, bind_map)
        instance_ids = [row[0] for row in cur.fetchall() if row[0]]
        return {'instance_ids': instance_ids}
    except Exception as exc:
        return {'error': f'Query failed: {exc}'}
    finally:
        conn.close()


def fetch_impl_ytt_wip(start_date, end_date, instance_ids=None):
    conn, err = get_oracle_connection()
    if err:
        return {'error': err}

    try:
        cur = conn.cursor()
        bind_map = {'start_date': start_date, 'end_date': end_date}

        if instance_ids:
            in_clause = _build_in_clause('selinst', instance_ids, bind_map)
            instance_filter_sim_admin = f'vgm.instance_id IN ({in_clause})'
            instance_filter_sims_new = f'vgm.instance_id IN ({in_clause})'
        else:
            where_sim_admin = _impl_ytt_wip_instance_hdr_where('SIM_ADMIN', bind_map, 'exclA')
            where_sims_new = _impl_ytt_wip_instance_hdr_where('SIMS_NEW', bind_map, 'exclB')
            instance_filter_sim_admin = f'vgm.instance_id IN (SELECT DISTINCT instance_id FROM SIM_ADMIN.INSTANCE_HDR WHERE {where_sim_admin})'
            instance_filter_sims_new = f'vgm.instance_id IN (SELECT DISTINCT instance_id FROM SIMS_NEW.INSTANCE_HDR WHERE {where_sims_new})'

        sql = f"""
        SELECT GROUP_DATE, instance_id, GROUP_ID, GROUP_NAME, TICKET_ID, CHANGE_IMPACT,
               PRIORITY, CHANGE_HISTORY, CREATED_BY, TEST_BY, TEST_STATUS, QA_TARGET_DATE,
               TEST_COMMENTS
        FROM (
            SELECT DISTINCT TO_CHAR(vgm.CREATE_TSTAMP, 'DD-MON-YYYY') GROUP_DATE,
                   vgm.instance_id AS instance_id,
                   vgm.GROUP_ID AS GROUP_ID,
                   vgm.GROUP_NAME AS GROUP_NAME,
                   cd.code_desc AS CHANGE_IMPACT,
                   vgm.ticket_id AS TICKET_ID,
                   cd1.code_desc AS PRIORITY,
                   vgm.CHANGE_HISTORY AS CHANGE_HISTORY,
                   up1.user_name AS CREATED_BY,
                   up.user_name AS TEST_BY,
                   cd2.code_desc AS TEST_STATUS,
                   NVL(TO_CHAR(vgs.QA_TARGET_DATE, 'DD-MON-YYYY'), ' ') AS QA_TARGET_DATE,
                   vgs.TEST_COMMENTS AS TEST_COMMENTS,
                   TRUNC(vgm.CREATE_TSTAMP) AS CREATE_TSTAMP
            FROM SIM_ADMIN.VERSION_GROUP_MAP vgm,
                 SIM_ADMIN.VERSION_GROUP_STATUS vgs,
                 SIM_ADMIN.USER_PROFILE up,
                 SIM_ADMIN.USER_PROFILE up1,
                 SIM_ADMIN.CODES_DTL cd,
                 SIM_ADMIN.CODES_DTL cd1,
                 SIM_ADMIN.CODES_DTL cd2
            WHERE vgm.instance_id = vgs.instance_id
              AND vgm.GROUP_ID = vgs.GROUP_ID
              AND cd.code_type = 'CHANGE_IMPACT'
              AND vgm.change_impact = cd.code_id
              AND cd1.code_type = 'PRIORITY'
              AND vgm.PRIORITY = cd1.code_id
              AND cd2.code_type = 'TEST_STATUS'
              AND vgs.test_status = cd2.code_id
              AND vgs.TEST_BY = up.user_id
              AND vgs.TEST_BY = up.user_id(+)
              AND vgm.CREATE_USER = up1.user_id
              AND {instance_filter_sim_admin}
              AND vgs.TEST_STATUS IN ('', '10', '20')
              AND vgs.VC_PATCH_NO = ' '
              AND TRUNC(vgs.create_tstamp) >= TO_DATE(:start_date, 'YYYY-MM-DD')
              AND TRUNC(vgs.create_tstamp) <= TO_DATE(:end_date, 'YYYY-MM-DD')
            UNION
            SELECT DISTINCT TO_CHAR(vgm.CREATE_TSTAMP, 'DD-MON-YYYY') GROUP_DATE,
                   vgm.instance_id AS instance_id,
                   vgm.GROUP_ID AS GROUP_ID,
                   ' ' AS GROUP_NAME,
                   cd.code_desc AS CHANGE_IMPACT,
                   vgm.ticket_id AS TICKET_ID,
                   cd1.code_desc AS PRIORITY,
                   vgm.CHANGE_HISTORY AS CHANGE_HISTORY,
                   up1.user_name AS CREATED_BY,
                   up.user_name AS TEST_BY,
                   cd2.code_desc AS TEST_STATUS,
                   NVL(TO_CHAR(vgs.QA_TARGET_DATE, 'DD-MON-YYYY'), ' ') AS QA_TARGET_DATE,
                   vgs.TEST_COMMENTS AS TEST_COMMENTS,
                   TRUNC(vgm.CREATE_TSTAMP) AS CREATE_TSTAMP
            FROM SIMS_NEW.VERSION_GROUP_MAP vgm,
                 SIMS_NEW.VERSION_GROUP_STATUS vgs,
                 SIMS_NEW.USER_PROFILE up,
                 SIMS_NEW.USER_PROFILE up1,
                 SIMS_NEW.CODES_DTL cd,
                 SIMS_NEW.CODES_DTL cd1,
                 SIMS_NEW.CODES_DTL cd2
            WHERE vgm.instance_id = vgs.instance_id
              AND vgm.GROUP_ID = vgs.GROUP_ID
              AND cd.code_type = 'CHANGE_IMPACT'
              AND vgm.change_impact = cd.code_id
              AND cd1.code_type = 'PRIORITY'
              AND vgm.PRIORITY = cd1.code_id
              AND cd2.code_type = 'TEST_STATUS'
              AND vgs.test_status = cd2.code_id
              AND vgs.TEST_BY = up.user_id
              AND vgs.TEST_BY = up.user_id(+)
              AND vgm.CREATE_USER = up1.user_id
              AND {instance_filter_sims_new}
              AND vgs.TEST_STATUS IN ('', '10', '20')
              AND vgs.VC_PATCH_NO = ' '
              AND TRUNC(vgs.create_tstamp) >= TO_DATE(:start_date, 'YYYY-MM-DD')
              AND TRUNC(vgs.create_tstamp) <= TO_DATE(:end_date, 'YYYY-MM-DD')
        )
        ORDER BY CREATE_TSTAMP
        """
        cur.execute(sql, bind_map)
        columns = [d[0] for d in cur.description] if cur.description else []

        rows = []
        for raw in cur:
            row_map = {}
            for idx, col in enumerate(columns):
                value = raw[idx]
                row_map[col] = str(value) if value is not None else None
            rows.append(row_map)

        logger.info('IMPL YTT WIP report generated: %d row(s).', len(rows))
        return {'columns': columns, 'rows': rows, 'row_count': len(rows)}
    except Exception as exc:
        logger.exception('IMPL YTT WIP report query failed.')
        return {'error': f'Query failed: {exc}'}
    finally:
        conn.close()

