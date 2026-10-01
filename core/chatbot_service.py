"""Chatbot conversation logic: intent routing and session state handling."""
import re

from flask import session

from .nlu import (
    parse_instance_and_patches,
    parse_instance_reference,
    is_instance_only_message,
    expand_alias,
    is_patch_owner_question,
    is_patch_list_request,
    is_group_tags_request,
    is_consolidation_request,
    is_qa_server_request,
    is_deployment_request,
    is_all_details_request,
    extract_stage_code,
    extract_group_tag,
    extract_filename_pattern,
    extract_patch_seq_no,
    extract_patch_seq_nos,
    extract_group_tags,
    is_files_in_group_tag_request,
    is_files_in_patch_request,
    is_req_extra_patch_request,
    is_deployment_request_by_stage,
    is_stage_reference_request,
    is_file_release_patch_request,
    is_group_tag_owner_request,
    is_test_deployment_pending_request,
    is_auto_patch_extracted_request,
    is_vc_shift_request,
    is_patch_seq_format,
    is_group_tag_format,
)
from .oracle_db import (
    fetch_table_details,
    _render_table_excerpt,
    fetch_client_server_map_details,
    _render_client_server_map_details,
    fetch_instance_candidates,
    resolve_instance_choice,
    _render_query_result,
    _build_extra_patch_alert_lines,
    fetch_files_by_group_tag,
    fetch_files_by_patch_numbers,
    fetch_req_patch_stage_details,
    fetch_file_release_patches,
    fetch_group_tag_owner,
    fetch_stage_reference,
    fetch_test_deployment_pending,
    fetch_auto_patch_extracted,
    fetch_patch_seq_flow,
    _resolve_stage_display,
    fetch_patch_numbers_for_instance,
    fetch_group_tags_for_instance,
    fetch_patch_owner,
    fetch_patch_details,
    fetch_group_tag_for_patch_seq,
    fetch_group_tag_for_patch_no,
    fetch_group_tag_deployment_details,
    fetch_group_tags_deployment_summary,
)
from .vc_shift import fetch_vc_shift_details, _render_vc_shift_chat_response


MENU_OPTIONS_TEXT = (
    'FC  - File Count\n'
    'DS  - Deploy Status\n'
    'DB  - Deployed By\n'
    'PN  - Patch Number\n'
    'FGT - Files In Group Tag\n'
    'FP  - Files In Patch\n'
    'RQ  - Request Details\n'
    'GO  - Group Tag Owner\n'
    'WTP - Who Took Patch'
)

OPTION_PROMPTS = {
    'FC': 'Please provide Group Tag or Patch Seq No or Patch No.',
    'DS': 'Please provide Group Tag or Patch Seq No or Patch No.',
    'DB': 'Please provide Group Tag.',
    'PN': 'Please provide Group Tag or Patch Seq No.',
    'FGT': 'Please provide Group Tag.',
    'FP': 'Please provide Patch Seq No or Patch No.',
    'RQ': 'Please provide Group Tag or Patch Seq No or Patch No.',
    'GO': 'Please provide Group Tag or Patch Seq No or Patch No.',
    'WTP': 'Please provide Group Tag or Patch Seq No or Patch No.',
}


def _classify_identifier(text):
    """Classify free-text input as ('group_tag'|'patch_seq'|'patch_no', value), priority Group Tag > Patch Seq No > Patch No.
    value is always a list (one or more comma/whitespace-separated identifiers), except for patch_no which is already a list of digit strings."""
    stripped = (text or '').strip()
    if is_patch_seq_format(stripped):
        return 'patch_seq', extract_patch_seq_nos(stripped)
    if is_group_tag_format(stripped):
        return 'group_tag', extract_group_tags(stripped)
    digits = re.findall(r'\d+', stripped)
    if digits:
        return 'patch_no', digits
    return None, None


def _resolve_group_tags_for_option(kind, value, instance_id):
    if kind == 'group_tag':
        return list(value), None

    if kind == 'patch_seq':
        group_ids = []
        seen = set()
        for patch_seq_no in value:
            lookup = fetch_group_tag_for_patch_seq(patch_seq_no)
            if lookup.get('error'):
                return None, lookup['error']
            for gid in lookup.get('group_ids') or []:
                if gid not in seen:
                    seen.add(gid)
                    group_ids.append(gid)
        return (group_ids, None) if group_ids else (None, 'No records found.')

    if kind == 'patch_no':
        group_ids = set()
        for patch_no in value:
            lookup = fetch_group_tag_for_patch_no(instance_id, patch_no)
            if lookup.get('error'):
                return None, lookup['error']
            group_ids.update(lookup.get('group_ids') or [])
        return (sorted(group_ids), None) if group_ids else (None, 'No records found.')

    return None, 'No records found.'


def _resolve_patch_numbers_for_option(kind, value, instance_id):
    if kind == 'patch_no':
        return value, None

    if kind == 'patch_seq':
        patch_nos = []
        seen = set()
        all_group_ids = []
        for patch_seq_no in value:
            seq_result = fetch_patch_seq_flow(patch_seq_no)
            if seq_result.get('error'):
                return None, seq_result['error']
            for pn in seq_result.get('patch_nos') or []:
                if pn not in seen:
                    seen.add(pn)
                    patch_nos.append(pn)
            all_group_ids.extend(seq_result.get('group_ids') or [])
        if patch_nos:
            return patch_nos, None
        if all_group_ids:
            summary = fetch_group_tags_deployment_summary(all_group_ids)
            patch_nos = summary.get('patch_numbers') or []
            if patch_nos:
                return patch_nos, None
        return None, 'No records found.'

    if kind == 'group_tag':
        patch_nos = []
        seen = set()
        for group_tag in value:
            details = fetch_group_tag_deployment_details(group_tag)
            if details.get('error'):
                return None, details['error']
            for pn in details.get('patch_numbers') or []:
                if pn not in seen:
                    seen.add(pn)
                    patch_nos.append(pn)
        return (patch_nos, None) if patch_nos else (None, 'No records found.')

    return None, 'No records found.'


def _run_menu_option(option, kind, value, instance_id):
    """Execute the query for a selected menu option (rules 4-8) and return the response text."""
    if option == 'FC':
        group_tags, err = _resolve_group_tags_for_option(kind, value, instance_id)
        if err:
            return err
        unique_files = []
        seen_files = set()
        for group_tag in group_tags:
            result = fetch_files_by_group_tag(instance_id, group_tag, max_rows=5000)
            if result.get('error'):
                return result['error']
            for row in result.get('rows', []):
                name = row.get('FILENAME')
                if not name:
                    continue
                key = name.upper()
                if key in seen_files:
                    continue
                seen_files.add(key)
                unique_files.append(name)
        if not unique_files:
            return 'No records found.'
        lines = [
            f'Instance ID: {instance_id}',
            f'Group Tag: {", ".join(group_tags)}',
            f'File Count (Unique): {len(unique_files)}',
            'Filenames:',
        ]
        lines.extend(unique_files)
        return '\n'.join(lines)

    if option == 'DS':
        group_tags, err = _resolve_group_tags_for_option(kind, value, instance_id)
        if err:
            return err
        details = fetch_group_tag_deployment_details(group_tags[0]) if len(group_tags) == 1 else fetch_group_tags_deployment_summary(group_tags)
        if details.get('error'):
            return details['error']
        if details.get('not_found'):
            return 'No records found.'

        deploy_status = details.get('deploy_status')
        deployed_by = details.get('deployed_by')
        patch_numbers = details.get('patch_numbers') or []
        deploy_status = ', '.join(deploy_status) if isinstance(deploy_status, list) else (deploy_status or 'N/A')
        deployed_by = ', '.join(deployed_by) if isinstance(deployed_by, list) else (deployed_by or 'N/A')

        return '\n'.join([
            f'Instance ID: {instance_id}',
            f'Group Tag: {", ".join(group_tags)}',
            f'Status: {deploy_status}',
            f'Patch Number: {", ".join(patch_numbers) if patch_numbers else "N/A"}',
            f'Deployed By: {deployed_by}',
        ])

    if option == 'DB':
        group_tags, err = _resolve_group_tags_for_option(kind, value, instance_id)
        if err:
            return err
        details = fetch_group_tag_deployment_details(group_tags[0]) if len(group_tags) == 1 else fetch_group_tags_deployment_summary(group_tags)
        if details.get('error'):
            return details['error']
        if details.get('not_found') or not details.get('deployed_by'):
            return 'No records found.'
        deployed_by = details['deployed_by']
        deployed_by = ', '.join(deployed_by) if isinstance(deployed_by, list) else deployed_by
        return f'Instance ID: {instance_id}\nGroup Tag: {", ".join(group_tags)}\nDeployed By: {deployed_by}'

    if option == 'PN':
        group_tags, err = _resolve_group_tags_for_option(kind, value, instance_id)
        if err:
            return err
        details = fetch_group_tag_deployment_details(group_tags[0]) if len(group_tags) == 1 else fetch_group_tags_deployment_summary(group_tags)
        if details.get('error'):
            return details['error']
        patch_numbers = details.get('patch_numbers') or []
        if not patch_numbers:
            return 'No records found.'
        return f'Instance ID: {instance_id}\nGroup Tag: {", ".join(group_tags)}\nPatch Number: {", ".join(patch_numbers)}'

    if option == 'FGT':
        group_tags, err = _resolve_group_tags_for_option(kind, value, instance_id)
        if err:
            return err
        lines = [f'Instance ID: {instance_id}', f'Group Tag: {", ".join(group_tags)}']
        found_any = False
        for group_tag in group_tags:
            result = fetch_files_by_group_tag(instance_id, group_tag)
            if result.get('error'):
                return result['error']
            if result.get('row_count', 0) == 0:
                continue
            found_any = True
            lines.append('')
            lines.append(f'Group Tag {group_tag}:')
            lines.extend(_render_query_result('Files In Group Tag', result))
        if not found_any:
            return 'No records found.'
        return '\n'.join(lines)

    if option == 'FP':
        if kind == 'patch_no':
            patch_nos = value
        else:
            patch_nos, err = _resolve_patch_numbers_for_option(kind, value, instance_id)
            if err:
                return err
        result = fetch_files_by_patch_numbers(instance_id, patch_nos)
        if result.get('error'):
            return result['error']
        if result.get('row_count', 0) == 0:
            return 'No records found.'
        lines = [f'Instance ID: {instance_id}', f'Patch No: {", ".join(patch_nos)}']
        lines.extend(_render_query_result('Files In Patch', result))
        return '\n'.join(lines)

    if option == 'RQ':
        patch_nos, err = _resolve_patch_numbers_for_option(kind, value, instance_id)
        if err:
            return err
        lines = [f'Instance ID: {instance_id}', f'Patch No: {", ".join(patch_nos)}']
        for patch_no in patch_nos:
            lines.append('')
            lines.append(f'Request Details for patch {patch_no}:')
            lines.extend(_render_table_excerpt(fetch_table_details('PATCH_INFO', instance_id=instance_id, patch_no=patch_no), 'PATCH_INFO'))
            lines.extend(_render_table_excerpt(fetch_table_details('PATCH_DEPLOYMENT_INFO', instance_id=instance_id, patch_no=patch_no), 'PATCH_DEPLOYMENT_INFO'))
        return '\n'.join(lines).strip()

    if option == 'GO':
        group_tags, err = _resolve_group_tags_for_option(kind, value, instance_id)
        if err:
            return err
        lines = [f'Instance ID: {instance_id}', f'Group Tag: {", ".join(group_tags)}']
        for group_tag in group_tags:
            lines.extend(_render_query_result(f'Group Tag Owner ({group_tag})', fetch_group_tag_owner(group_tag)))
        return '\n'.join(lines)

    if option == 'WTP':
        patch_nos, err = _resolve_patch_numbers_for_option(kind, value, instance_id)
        if err:
            return err
        lines = [f'Instance ID: {instance_id}']
        for patch_no in patch_nos:
            owner_result = fetch_patch_owner(instance_id, patch_no)
            if owner_result.get('error'):
                lines.append(f'Patch {patch_no}: {owner_result["error"]}')
                continue
            lines.append(f'Patch No: {patch_no}')
            if not owner_result['builder_ids']:
                lines.append('Who Took Patch: not found in PATCH_DETAILS')
            else:
                for builder_id in owner_result['builder_ids']:
                    name = owner_result['builder_names'].get(builder_id)
                    lines.append(f'- {builder_id} : {name}' if name else f'- {builder_id} : name not found in USER_PROFILE')
            lines.append('')
        text_out = '\n'.join(lines).strip()
        return text_out or 'No records found.'

    return 'No records found.'


def _render_group_tag_deployment_response(group_tag, details, patch_seq_no=None):
    if details.get('error'):
        return details['error']
    if details.get('not_found'):
        return 'No deployment details found for the provided input.'

    lines = []
    if patch_seq_no:
        lines.append(f'Patch Sequence Number: {patch_seq_no}')
    lines.append(f'Group Tag: {group_tag}')

    patch_numbers = details.get('patch_numbers') or []
    lines.append(f'File Count: {details.get("file_count", 0)}')
    lines.append(f'Deploy Status: {details.get("deploy_status") or "N/A"}')
    lines.append(f'Deployed By: {details.get("deployed_by") or "N/A"}')
    lines.append(f'Patch Number: {", ".join(patch_numbers) if patch_numbers else "N/A"}')
    return '\n'.join(lines)


def _render_group_tags_summary_response(group_tags, details, patch_seq_no=None):
    if details.get('error'):
        return details['error']
    if details.get('not_found'):
        return 'No deployment details found for the provided input.'

    lines = []
    if patch_seq_no:
        lines.append(f'Patch Sequence Number: {patch_seq_no}')
    lines.append(f'Group Tag: {", ".join(group_tags)}')

    deploy_status = details.get('deploy_status') or []
    deployed_by = details.get('deployed_by') or []
    patch_numbers = details.get('patch_numbers') or []
    lines.append(f'File Count (Unique): {details.get("file_count", 0)}')
    lines.append(f'Deploy Status: {", ".join(deploy_status) if deploy_status else "N/A"}')
    lines.append(f'Deployed By: {", ".join(deployed_by) if deployed_by else "N/A"}')
    lines.append(f'Patch Number: {", ".join(patch_numbers) if patch_numbers else "N/A"}')
    return '\n'.join(lines)


def _handle_pending_input(message, pending_input, active_instance):
    if not pending_input:
        return None, active_instance, None

    intent = pending_input.get('intent')
    instance_id = pending_input.get('instance_id') or active_instance

    if intent == 'menu_option':
        option = pending_input.get('option')
        kind, value = _classify_identifier(message)
        if not kind:
            return 'Please provide a valid Group Tag, Patch Sequence Number, or Patch No.', active_instance, pending_input

        return _run_menu_option(option, kind, value, instance_id), instance_id, None

    if intent == 'files_group':
        group_tag = extract_group_tag(message)
        if not group_tag:
            return 'Please provide Group Tag (example: DOMS261-0394).', active_instance, pending_input

        result = fetch_files_by_group_tag(instance_id, group_tag)
        lines = [f'Instance ID: {instance_id}', f'Group Tag: {group_tag}']
        lines.extend(_render_query_result('Files In Group Tag', result))
        return '\n'.join(lines), instance_id, None

    if intent == 'files_patch':
        patch_nos = parse_instance_and_patches(message)[1]
        if not patch_nos:
            return 'Please provide Patch No (single or comma-separated).', active_instance, pending_input

        result = fetch_files_by_patch_numbers(instance_id, patch_nos)
        lines = [f'Instance ID: {instance_id}', f'Patch No: {", ".join(patch_nos)}']
        lines.extend(_render_query_result('Files In Patch', result))
        return '\n'.join(lines), instance_id, None

    if intent in ('req_extra', 'deployment_stage'):
        patch_nos = pending_input.get('patch_nos')
        stage_code = pending_input.get('stage_code')

        if not patch_nos:
            patch_nos = parse_instance_and_patches(message)[1]
            if not patch_nos:
                return 'Please provide Patch No (single or comma-separated).', active_instance, pending_input
            pending_input['patch_nos'] = patch_nos
            return 'Provide Stage (example: UAT, PROD, or stage number like 20/40).', active_instance, pending_input

        if not stage_code:
            stage_code = extract_stage_code(message)
            if not stage_code:
                return 'Provide valid Stage (UAT, PROD, QA, DEV-UAT, or stage number).', active_instance, pending_input
            pending_input['stage_code'] = stage_code

        result = fetch_req_patch_stage_details(instance_id, patch_nos, stage_code)
        title = 'Ensure Req No Extra Patches' if intent == 'req_extra' else 'Deployment Request'
        lines = [
            f'Instance ID: {instance_id}',
            f'Patch No: {", ".join(patch_nos)}',
            f'Stage: {_resolve_stage_display(stage_code)} ({stage_code})',
        ]
        lines.extend(_render_query_result(title, result))
        lines.extend(_build_extra_patch_alert_lines(result, patch_nos))
        return '\n'.join(lines), instance_id, None

    if intent == 'file_release':
        filename_pattern = extract_filename_pattern(message) or (message or '').strip()
        if not filename_pattern:
            return 'Please provide file name pattern.', active_instance, pending_input

        result = fetch_file_release_patches(instance_id, filename_pattern)
        lines = [f'Instance ID: {instance_id}', f'Filename Pattern: {filename_pattern}']
        lines.extend(_render_query_result('File Released In Patches', result))
        return '\n'.join(lines), instance_id, None

    if intent == 'group_owner':
        group_tag = extract_group_tag(message)
        if not group_tag:
            return 'Please provide Group Tag.', active_instance, pending_input

        result = fetch_group_tag_owner(group_tag)
        lines = [f'Group Tag: {group_tag}']
        lines.extend(_render_query_result('Group Tag Given By', result))
        return '\n'.join(lines), instance_id, None

    return None, active_instance, None


def build_chat_response(message, active_instance=None, skip_instance_resolution=False):
    message = expand_alias(message)
    text = (message or '').strip()

    # Menu option codes must win before instance-reference parsing mistakes them for an instance search.
    if active_instance and text.upper() in OPTION_PROMPTS:
        option = text.upper()
        return (
            OPTION_PROMPTS[option],
            active_instance,
            None,
            {'intent': 'menu_option', 'option': option, 'instance_id': active_instance},
        )

    parsed_instance, patch_nos = parse_instance_and_patches(text)
    instance_ref = parse_instance_reference(text)
    patch_seq_nos = extract_patch_seq_nos(text)
    patch_seq_no = patch_seq_nos[0] if patch_seq_nos else None

    if skip_instance_resolution:
        resolved_instance = active_instance
        pending = None
    else:
        provided_instance = None if (patch_seq_no or is_group_tag_format(text)) else (parsed_instance or instance_ref)
        pending = None
        resolved_instance = active_instance

        if provided_instance:
            search_result = fetch_instance_candidates(provided_instance)
            if search_result.get('error'):
                return search_result['error'], active_instance, None, None

            candidates = search_result['candidates']
            if not candidates:
                return f'No instance found for "{provided_instance}".', active_instance, None, None

            ids = [item['instance_id'] for item in candidates]
            exact = [iid for iid in ids if iid.upper() == provided_instance.upper()]
            unique_ids = []
            seen = set()
            for iid in ids:
                key = iid.upper()
                if key in seen:
                    continue
                seen.add(key)
                unique_ids.append(iid)

            if exact:
                resolved_instance = exact[0]
            elif len(unique_ids) == 1:
                resolved_instance = unique_ids[0]
            else:
                lines = [f'Multiple instance IDs found for "{provided_instance}". Please choose one:']
                for idx, iid in enumerate(unique_ids, start=1):
                    lines.append(f'{idx}. {iid}')
                lines.append('Reply with number or exact instance ID.')
                pending = {'instances': unique_ids, 'message': text}
                return '\n'.join(lines), active_instance, pending, None

    if resolved_instance and text.strip().upper() in OPTION_PROMPTS:
        option = text.strip().upper()
        return (
            OPTION_PROMPTS[option],
            resolved_instance,
            None,
            {'intent': 'menu_option', 'option': option, 'instance_id': resolved_instance},
        )

    if is_stage_reference_request(text):
        stage_result = fetch_stage_reference()
        lines = ['Patch Status Stages:']
        lines.extend(_render_query_result('CODES_DTL', stage_result, row_limit=40))
        return '\n'.join(lines), resolved_instance, None, None

    if is_test_deployment_pending_request(text):
        result = fetch_test_deployment_pending()
        lines = ['Test Deployment Pending (last 150 days):']
        lines.extend(_render_query_result('TEST_DEPLOYMENT', result))
        return '\n'.join(lines), resolved_instance, None, None

    if is_auto_patch_extracted_request(text):
        result = fetch_auto_patch_extracted()
        lines = ['Auto Patch Extracted (last 30 days):']
        lines.extend(_render_query_result('AUTO_PATCH', result))
        return '\n'.join(lines), resolved_instance, None, None

    if is_vc_shift_request(text):
        vc_shift = fetch_vc_shift_details()
        if not session.get('is_admin'):
            vc_shift = {**vc_shift, 'reviewer': None, 'reviewer_available': False}
        return _render_vc_shift_chat_response(vc_shift), resolved_instance, None, None

    if is_group_tag_format(text):
        group_tags = extract_group_tags(text)
        if len(group_tags) == 1:
            details = fetch_group_tag_deployment_details(group_tags[0])
            return _render_group_tag_deployment_response(group_tags[0], details), resolved_instance, None, None
        summary = fetch_group_tags_deployment_summary(group_tags)
        return _render_group_tags_summary_response(group_tags, summary), resolved_instance, None, None

    if patch_seq_nos and is_patch_seq_format(text):
        group_ids = []
        seen_group_ids = set()
        for seq in patch_seq_nos:
            seq_lookup = fetch_group_tag_for_patch_seq(seq)
            if seq_lookup.get('error'):
                return seq_lookup['error'], resolved_instance, None, None
            for gid in seq_lookup.get('group_ids') or []:
                if gid not in seen_group_ids:
                    seen_group_ids.add(gid)
                    group_ids.append(gid)

        if not group_ids:
            return 'No deployment details found for the provided input.', resolved_instance, None, None

        summary = fetch_group_tags_deployment_summary(group_ids)
        return (
            _render_group_tags_summary_response(group_ids, summary, patch_seq_no=', '.join(patch_seq_nos)),
            resolved_instance,
            None,
            None,
        )

    if patch_seq_nos:
        all_lines = []
        for idx, seq in enumerate(patch_seq_nos):
            seq_result = fetch_patch_seq_flow(seq)
            if seq_result.get('error'):
                return seq_result['error'], resolved_instance, None, None

            if seq_result.get('instance_id'):
                resolved_instance = seq_result['instance_id']

            lines = [
                f'Patch Seq No: {seq}',
                f'Instance: {seq_result.get("instance_id") or seq_result.get("instance_from_seq") or "N/A"}',
            ]

            if seq_result.get('group_ids'):
                lines.append(f'Group Tag(s): {", ".join(seq_result["group_ids"])}')

            version_rows = seq_result.get('versionhistory', {}).get('rows', [])
            unique_files = []
            seen_files = set()
            for row in version_rows:
                name = row.get('FILENAME')
                if not name:
                    continue
                key = name.upper()
                if key in seen_files:
                    continue
                seen_files.add(key)
                unique_files.append(name)

            lines.append('Filename:')
            if unique_files:
                for name in unique_files[:50]:
                    lines.append(name)
            else:
                lines.append('No files found')

            seq_patch_nos = seq_result.get('patch_nos') or []
            if seq_patch_nos:
                lines.append(f'Patch No(s): {", ".join(seq_patch_nos)}')
            else:
                log_rows = seq_result.get('patch_build_log', {}).get('rows', [])
                build_start = None
                patch_extract_flag = None
                if log_rows:
                    latest = log_rows[0]
                    build_start = latest.get('BUILD_START')
                    patch_extract_flag = latest.get('PATCH_EXTRACT_FLAG')

                if build_start is not None or patch_extract_flag is not None:
                    lines.append(
                        f'Patch No(s):  (BUILD_START={build_start or "N/A"} and PATCH_EXTRACT_FLAG={patch_extract_flag or "N/A"})'
                    )
                else:
                    lines.append('Patch No(s):  (BUILD_START=N/A and PATCH_EXTRACT_FLAG=N/A)')

            if idx:
                all_lines.append('')
            all_lines.extend(lines)
        return '\n'.join(all_lines), resolved_instance, None, None

    if resolved_instance and is_instance_only_message(text, resolved_instance):
        return f'Please choose an option:\n{MENU_OPTIONS_TEXT}', resolved_instance, None, None

    instance_id = resolved_instance

    if not instance_id:
        return 'Please provide the Instance ID.', active_instance, None, None

    if is_group_tag_owner_request(text):
        group_tag = extract_group_tag(text)
        if not group_tag:
            return 'Provide Group Tag.', instance_id, None, {'intent': 'group_owner', 'instance_id': instance_id}

        result = fetch_group_tag_owner(group_tag)
        lines = [f'Group Tag: {group_tag}']
        lines.extend(_render_query_result('Group Tag Given By', result))
        return '\n'.join(lines), instance_id, None, None

    if is_files_in_group_tag_request(text):
        group_tag = extract_group_tag(text)
        if not group_tag:
            return f'Instance ID: {instance_id}\nProvide Group Tag (example: DOMS261-0394).', instance_id, None, {'intent': 'files_group', 'instance_id': instance_id}

        result = fetch_files_by_group_tag(instance_id, group_tag)
        lines = [f'Instance ID: {instance_id}', f'Group Tag: {group_tag}']
        lines.extend(_render_query_result('Files In Group Tag', result))
        return '\n'.join(lines), instance_id, None, None

    if is_files_in_patch_request(text):
        if not patch_nos:
            return f'Instance ID: {instance_id}\nProvide Patch No (single or comma-separated).', instance_id, None, {'intent': 'files_patch', 'instance_id': instance_id}

        result = fetch_files_by_patch_numbers(instance_id, patch_nos)
        lines = [f'Instance ID: {instance_id}', f'Patch No: {", ".join(patch_nos)}']
        lines.extend(_render_query_result('Files In Patch', result))
        return '\n'.join(lines), instance_id, None, None

    if is_req_extra_patch_request(text):
        if not patch_nos:
            return f'Instance ID: {instance_id}\nProvide Patch No (single or comma-separated).', instance_id, None, {'intent': 'req_extra', 'instance_id': instance_id}

        stage_code = extract_stage_code(text)
        if not stage_code:
            return f'Instance ID: {instance_id}\nPatch No: {", ".join(patch_nos)}\nProvide Stage (UAT, PROD, etc).', instance_id, None, {'intent': 'req_extra', 'instance_id': instance_id, 'patch_nos': patch_nos}

        result = fetch_req_patch_stage_details(instance_id, patch_nos, stage_code)
        lines = [f'Instance ID: {instance_id}', f'Patch No: {", ".join(patch_nos)}', f'Stage: {_resolve_stage_display(stage_code)} ({stage_code})']
        lines.extend(_render_query_result('Ensure Req No Extra Patches', result))
        lines.extend(_build_extra_patch_alert_lines(result, patch_nos))
        return '\n'.join(lines), instance_id, None, None

    if is_deployment_request_by_stage(text):
        if not patch_nos:
            return f'Instance ID: {instance_id}\nProvide Patch No (single or comma-separated).', instance_id, None, {'intent': 'deployment_stage', 'instance_id': instance_id}

        stage_code = extract_stage_code(text)
        if not stage_code:
            return f'Instance ID: {instance_id}\nPatch No: {", ".join(patch_nos)}\nProvide Stage (UAT, PROD, etc).', instance_id, None, {'intent': 'deployment_stage', 'instance_id': instance_id, 'patch_nos': patch_nos}

        result = fetch_req_patch_stage_details(instance_id, patch_nos, stage_code)
        lines = [f'Instance ID: {instance_id}', f'Patch No: {", ".join(patch_nos)}', f'Stage: {_resolve_stage_display(stage_code)} ({stage_code})']
        lines.extend(_render_query_result('Deployment Request', result))
        lines.extend(_build_extra_patch_alert_lines(result, patch_nos))
        return '\n'.join(lines), instance_id, None, None

    if is_file_release_patch_request(text):
        filename_pattern = extract_filename_pattern(text)
        if not filename_pattern:
            return f'Instance ID: {instance_id}\nProvide file name (example: AcceptSSCCLeftAutomationProcess.java).', instance_id, None, {'intent': 'file_release', 'instance_id': instance_id}

        result = fetch_file_release_patches(instance_id, filename_pattern)
        lines = [f'Instance ID: {instance_id}', f'Filename Pattern: {filename_pattern}']
        lines.extend(_render_query_result('File Released In Patches', result))
        return '\n'.join(lines), instance_id, None, None

    need_consolidation = is_consolidation_request(text) or is_all_details_request(text)
    need_qa = is_qa_server_request(text) or is_all_details_request(text)
    need_deployment = is_deployment_request(text) or is_all_details_request(text)

    if need_consolidation or need_qa or need_deployment:
        lines = [f'Instance ID: {instance_id}']

        if need_consolidation:
            lines.append('')
            lines.extend(_render_table_excerpt(fetch_table_details('CLIENT_SERVER_INSTANCE_DTL', instance_id=instance_id), 'CLIENT_SERVER_INSTANCE_DTL'))

        if need_qa:
            lines.append('')
            qa_result = fetch_client_server_map_details(instance_id=instance_id, qa_only=True)
            if not qa_result.get('error') and qa_result.get('row_count', 0) == 0:
                fallback_result = fetch_client_server_map_details(instance_id=instance_id, qa_only=False)
                if not fallback_result.get('error') and fallback_result.get('row_count', 0) > 0:
                    fallback_result.setdefault('warnings', []).append('No rows matched QA keyword filter; showing all CLIENT_SERVER_MAP rows for this instance.')
                    qa_result = fallback_result
            lines.extend(_render_client_server_map_details(qa_result, 'CLIENT_SERVER_MAP (QA)'))

        if need_deployment:
            deployment_patches = patch_nos or [None]
            for patch_no in deployment_patches:
                lines.append('')
                lines.append('Deployment details (instance level):' if patch_no is None else f'Deployment details for patch {patch_no}:')
                lines.extend(_render_table_excerpt(fetch_table_details('PATCH_DEPLOYMENT_INFO', instance_id=instance_id, patch_no=patch_no), 'PATCH_DEPLOYMENT_INFO'))
                lines.extend(_render_table_excerpt(fetch_table_details('PATCH_INFO', instance_id=instance_id, patch_no=patch_no), 'PATCH_INFO'))

        return '\n'.join(lines).strip(), instance_id, None, None

    if is_patch_owner_question(text):
        if not patch_nos:
            return f'Instance ID: {instance_id}\nPlease provide patch number(s) for owner lookup.', instance_id, None, None

        lines = [f'Instance ID: {instance_id}']
        for patch_no in patch_nos:
            owner_result = fetch_patch_owner(instance_id, patch_no)
            if owner_result.get('error'):
                lines.append(f'Patch {patch_no}: {owner_result["error"]}')
                lines.append('')
                continue

            lines.append(f'Patch No: {patch_no}')
            lines.append(f'Build Column: {owner_result["build_column"]}')

            if not owner_result['builder_ids']:
                lines.append('Patch Taken By: not found in PATCH_DETAILS')
                lines.append('')
                continue

            lines.append('Patch Taken By:')
            for builder_id in owner_result['builder_ids']:
                name = owner_result['builder_names'].get(builder_id)
                lines.append(f'- {builder_id} : {name}' if name else f'- {builder_id} : name not found in USER_PROFILE')
            lines.append('')

        return '\n'.join(lines).strip(), instance_id, None, None

    if is_patch_list_request(text) and not patch_nos:
        patch_result = fetch_patch_numbers_for_instance(instance_id)
        if patch_result.get('error'):
            return patch_result['error'], instance_id, None, None
        if not patch_result['patch_nos']:
            return f'Instance ID: {instance_id}\nNo patch numbers found.', instance_id, None, None

        lines = [f'Instance ID: {instance_id}', f'Patch Nos ({len(patch_result["patch_nos"])}):']
        for patch_no in patch_result['patch_nos']:
            lines.append(f'- {patch_no}')
        return '\n'.join(lines), instance_id, None, None

    if is_group_tags_request(text) and not patch_nos:
        tag_result = fetch_group_tags_for_instance(instance_id)
        if tag_result.get('error'):
            return tag_result['error'], instance_id, None, None
        if not tag_result['group_ids']:
            return f'Instance ID: {instance_id}\nNo group tags found.', instance_id, None, None

        lines = [f'Instance ID: {instance_id}', f'Group Tags ({len(tag_result["group_ids"])}):']
        for gid in tag_result['group_ids']:
            lines.append(f'- {gid}')
        return '\n'.join(lines), instance_id, None, None

    if not patch_nos:
        return f'Instance ID: {instance_id}\nPlease choose an option:\n{MENU_OPTIONS_TEXT}', instance_id, None, None

    lines = [f'Instance ID: {instance_id}']
    for patch_no in patch_nos:
        result = fetch_patch_details(instance_id, patch_no)
        if result.get('error'):
            lines.append(f'Patch {patch_no}: {result["error"]}')
            lines.append('')
            continue

        if not result['group_ids']:
            lines.append(f'Patch {patch_no}: No PATCH_DETAILS row found.')
            lines.append('')
            continue

        lines.append(f'Patch No: {patch_no}')
        lines.append(f'Group IDs: {", ".join(result["group_ids"])}')
        lines.append(f'Serial Nos: {len(result["serial_nos"])}')
        lines.append(f'Filenames ({len(result["filenames"])} rows, {len(result["unique_filenames"])} unique):')
        if result['unique_filenames']:
            for name in result['unique_filenames']:
                lines.append(f'- {name}')
        else:
            lines.append('- No filenames found in VERSIONHISTORY')
        lines.append('')

    return '\n'.join(lines).strip(), instance_id, None, None


def process_chat_message(message):
    history = session.get('chat_history', [])
    active_instance = session.get('chat_instance_id')
    pending_instances = session.get('chat_pending_instances')
    pending_message = session.get('chat_pending_message')
    pending_input = session.get('chat_pending_input')

    bot_text = ''
    if message:
        history.append({'role': 'user', 'text': message})

        # VC shift/reviewer queries should work without instance context.
        if is_vc_shift_request(message):
            session.pop('chat_pending_instances', None)
            session.pop('chat_pending_message', None)
            session.pop('chat_pending_input', None)
            vc_shift = fetch_vc_shift_details()
            if not session.get('is_admin'):
                vc_shift = {**vc_shift, 'reviewer': None, 'reviewer_available': False}
            bot_text = _render_vc_shift_chat_response(vc_shift)
            history.append({'role': 'assistant', 'text': bot_text})
            history = history[-20:]
            session['chat_history'] = history
            if active_instance:
                session['chat_instance_id'] = active_instance
            else:
                session.pop('chat_instance_id', None)
            return bot_text, history

        if pending_input:
            bot_text, active_instance, next_pending_input = _handle_pending_input(message, pending_input, active_instance)
            if next_pending_input:
                session['chat_pending_input'] = next_pending_input
            else:
                session.pop('chat_pending_input', None)

            if bot_text is None:
                bot_text, active_instance, pending, pending_input_next = build_chat_response(message, active_instance)
                if pending:
                    session['chat_pending_instances'] = pending['instances']
                    session['chat_pending_message'] = pending['message']
                if pending_input_next:
                    session['chat_pending_input'] = pending_input_next

        elif pending_instances:
            selected = resolve_instance_choice(message, pending_instances)
            if selected:
                active_instance = selected
                session.pop('chat_pending_instances', None)
                session.pop('chat_pending_message', None)

                if pending_message:
                    follow_text, active_instance, pending, pending_input_next = build_chat_response(
                        pending_message,
                        active_instance,
                        skip_instance_resolution=True,
                    )
                    bot_text = f'Instance selected: {active_instance}\n{follow_text}'
                    if pending:
                        session['chat_pending_instances'] = pending['instances']
                        session['chat_pending_message'] = pending['message']
                    if pending_input_next:
                        session['chat_pending_input'] = pending_input_next
                else:
                    bot_text = f'Please choose an option:\n{MENU_OPTIONS_TEXT}'
            else:
                lines = ['Please choose a valid instance by number or exact ID:']
                for idx, iid in enumerate(pending_instances, start=1):
                    lines.append(f'{idx}. {iid}')
                bot_text = '\n'.join(lines)

        else:
            bot_text, active_instance, pending, pending_input_next = build_chat_response(message, active_instance)
            if pending:
                session['chat_pending_instances'] = pending['instances']
                session['chat_pending_message'] = pending['message']
            if pending_input_next:
                session['chat_pending_input'] = pending_input_next
            else:
                session.pop('chat_pending_input', None)

        history.append({'role': 'assistant', 'text': bot_text})
        history = history[-20:]
        session['chat_history'] = history
        if active_instance:
            session['chat_instance_id'] = active_instance
        else:
            session.pop('chat_instance_id', None)

    return bot_text, history
