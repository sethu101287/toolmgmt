"""Chatbot NLU helpers: parse and classify free-text messages."""
import re

from .appcore import STAGE_NAME_TO_CODE

# Short-code aliases (not part of the main menu) mapped to a phrase the existing
# intent detectors recognize. The 9 main menu options (FC, DS, DB, PN, FGT, FP,
# RQ, GO, WTP) are handled by the dedicated menu-option flow in chatbot_service.py.
ALIAS_MAP = {
    'DBS': 'deployment by stage',
    'FRPT': 'file released patch trace',
    'PNL': 'patch no list',
    'GT': 'group tags',
    'SR': 'stage no reference',
    'TDP': 'test deployment pending',
    'APE': 'auto patch extracted',
    'VCSP': 'vc shift plan',
}


def expand_alias(message):
    """If message is exactly a known short code (e.g. 'FGT'), expand it to its full phrase."""
    text = (message or '').strip()
    return ALIAS_MAP.get(text.upper(), message)


def parse_instance_and_patches(message):
    text = (message or '').strip()

    patch_numbers = []
    patch_anchor = re.search(r'patch(?:\s*no)?\s*[:=\-]?\s*([0-9,\s]+)', text, re.IGNORECASE)
    if patch_anchor:
        patch_numbers = re.findall(r'\d+', patch_anchor.group(1))
    if not patch_numbers:
        patch_numbers = re.findall(r'\b\d+\b', text)

    dedup_patches = []
    seen = set()
    for patch in patch_numbers:
        if patch not in seen:
            seen.add(patch)
            dedup_patches.append(patch)

    instance_match = re.search(
        r'instance(?:\s*id)?\s*[:=\-]?\s*([A-Za-z0-9_\-]+)',
        text,
        re.IGNORECASE,
    )
    if not instance_match:
        instance_match = re.search(r'\b(?:of|for|in)\s+([A-Za-z][A-Za-z0-9_\-]+)\b', text, re.IGNORECASE)

    instance_id = instance_match.group(1).upper() if instance_match else None
    return instance_id, dedup_patches


def parse_instance_reference(message):
    text = (message or '').strip()
    match = re.search(r'instance(?:\s*id)?\s*[:=\-]?\s*([A-Za-z0-9_\-]+)', text, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    single = re.fullmatch(r'[A-Za-z][A-Za-z0-9_\-]*', text)
    if single:
        return text.upper()

    tail = re.search(r'\b(?:of|for|in)\s+([A-Za-z][A-Za-z0-9_\-]+)\b\s*$', text, re.IGNORECASE)
    if tail:
        return tail.group(1).upper()
    return None


def is_instance_only_message(message, instance_id):
    text = (message or '').strip()
    lower = text.lower()
    if any(token in lower for token in ('patch', 'file', 'group', 'tag', 'who', 'build', 'taken', 'take')):
        return False

    if text.upper() == instance_id:
        return True

    return bool(re.fullmatch(r'instance(?:\s*id)?\s*[:=\-]?\s*[A-Za-z0-9_\-]+', text, re.IGNORECASE))


def is_patch_owner_question(message):
    text = (message or '').lower()
    return 'who' in text and 'patch' in text and any(token in text for token in ('take', 'took', 'taken', 'build', 'built'))


def is_patch_list_request(message):
    text = (message or '').lower()
    return 'patch no' in text or 'patch number' in text or text.strip() in ('patch', 'patches')


def is_group_tags_request(message):
    text = (message or '').lower()
    return 'group tag' in text or 'group tags' in text or 'group id' in text or text.strip() in ('group', 'groups')



def is_consolidation_request(message):
    text = (message or '').lower()
    return any(token in text for token in ('consolidation', 'client_server_instance_dtl', 'instance detail'))


def is_qa_server_request(message):
    text = (message or '').lower()
    return any(token in text for token in ('qa server', 'qa details', 'client_server_map'))


def _normalize_intent_text(message):
    return re.sub(r'[^a-z0-9]+', ' ', (message or '').lower()).strip()


def _is_deployment_alias(message):
    text = _normalize_intent_text(message)
    compact = text.replace(' ', '')

    exact_aliases = {
        'dep req',
        'deployment request',
        'deployment reques',
        'deployment',
        'dep',
        'request',
        'req',
    }
    compact_aliases = {
        'depreq',
        'deploymentrequest',
        'deploymentreques',
        'deployment',
        'dep',
        'request',
        'req',
    }

    if text in exact_aliases or compact in compact_aliases:
        return True

    return any(token in text for token in ('deploy req', 'deployment request', 'patch deployment', 'patch info'))


def is_deployment_request(message):
    return _is_deployment_alias(message)


def is_all_details_request(message):
    text = (message or '').lower()
    return any(token in text for token in ('all details', 'complete details', 'full details', 'everything'))

def _normalize_stage_token(token):
    return re.sub(r'[^A-Z0-9]+', '', (token or '').upper())


def _stage_lookup_map():
    data = {}
    for name, code in STAGE_NAME_TO_CODE.items():
        data[_normalize_stage_token(name)] = code
    return data


def extract_stage_code(message):
    text = (message or '').strip()
    if not text:
        return None

    exact_num = re.search(r'\b(\d{1,3})\b', text)
    if exact_num:
        return exact_num.group(1)

    normalized = _normalize_stage_token(text)
    lookup = _stage_lookup_map()
    if normalized in lookup:
        return lookup[normalized]

    for name, code in STAGE_NAME_TO_CODE.items():
        if _normalize_stage_token(name) in normalized:
            return code
    return None


def extract_group_tag(message):
    text = (message or '').strip()
    if not text:
        return None

    match = re.search(r'group(?:\s*tag|\s*id)?\s*[:=\-]?\s*([A-Za-z0-9_\-]+)', text, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    plain = re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_\-]*', text)
    if plain:
        return text.upper()
    return None


def extract_filename_pattern(message):
    text = (message or '').strip()
    quoted = re.search(r'["\']([^"\']+)["\']', text)
    if quoted:
        return quoted.group(1)

    match = re.search(r'file(?:name)?\s*[:=\-]?\s*([^,]+)$', text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None



def extract_patch_seq_no(message):
    text = (message or '').strip().upper()
    if not text:
        return None

    exact = re.search(r'\b([A-Z0-9]+-P-[0-9]{3,})\b', text)
    if exact:
        return exact.group(1)

    match = re.search(r'patch\s*seq\s*no\s*[:=\-]?\s*([A-Z0-9]+-P-[0-9]{3,})', text, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return None


def _split_tokens(message):
    """Split a message into comma/whitespace-separated tokens, dropping empties."""
    return [t for t in re.split(r'[,\s]+', (message or '').strip()) if t]


def extract_patch_seq_nos(message):
    """Return all distinct <INSTANCE_ID>-P-<NUMBER> tokens found in message, in order."""
    tokens = _split_tokens(message)
    seen = set()
    result = []
    for token in tokens:
        if re.fullmatch(r'[A-Za-z0-9]+-P-[0-9]{3,}', token, re.IGNORECASE):
            value = token.upper()
            if value not in seen:
                seen.add(value)
                result.append(value)
    return result


def extract_group_tags(message):
    """Return all distinct <INSTANCE_ID>-<NUMBER> tokens found in message, in order."""
    tokens = _split_tokens(message)
    seen = set()
    result = []
    for token in tokens:
        if re.fullmatch(r'[A-Za-z0-9]+-[0-9]+', token):
            value = token.upper()
            if value not in seen:
                seen.add(value)
                result.append(value)
    return result


def is_patch_seq_format(message):
    """Whole message is one or more comma/whitespace-separated <INSTANCE_ID>-P-<NUMBER> tokens."""
    tokens = _split_tokens(message)
    return bool(tokens) and all(re.fullmatch(r'[A-Za-z0-9]+-P-[0-9]+', t, re.IGNORECASE) for t in tokens)


def is_group_tag_format(message):
    """Whole message is one or more comma/whitespace-separated <INSTANCE_ID>-<NUMBER> tokens."""
    tokens = _split_tokens(message)
    return bool(tokens) and all(re.fullmatch(r'[A-Za-z0-9]+-[0-9]+', t) for t in tokens)


def is_plain_instance_id_format(message):
    """Whole message is exactly <INSTANCE_ID> with no group tag/patch seq suffix."""
    return bool(re.fullmatch(r'[A-Za-z][A-Za-z0-9]*', (message or '').strip()))


def parse_patch_seq_parts(patch_seq_no):
    if not patch_seq_no:
        return None, None, None

    match = re.match(r'^([A-Z0-9]+)-([A-Z])-([0-9]{3,})$', patch_seq_no.upper())
    if not match:
        return None, None, None
    return match.group(1), match.group(2), match.group(3)

def is_files_in_group_tag_request(message):
    text = (message or '').lower()
    return 'group tag' in text and 'file' in text


def is_files_in_patch_request(message):
    text = (message or '').lower()
    return ('file' in text and 'patch' in text) or 'files in patch' in text


def is_req_extra_patch_request(message):
    text = (message or '').lower()
    return 'extra patch' in text or ('req no' in text and 'patch' in text) or ('request no' in text and 'patch' in text)


def is_deployment_request_by_stage(message):
    text = _normalize_intent_text(message)
    return _is_deployment_alias(message) or ('deploy' in text and 'stage' in text)


def is_stage_reference_request(message):
    text = (message or '').lower()
    return 'stage no' in text or 'patch status' in text


def is_file_release_patch_request(message):
    text = (message or '').lower()
    return 'file released' in text or ('filename' in text and 'patch' in text)


def is_group_tag_owner_request(message):
    text = (message or '').lower()
    return 'grouptag given by' in text or ('group tag' in text and 'given by' in text)


def is_test_deployment_pending_request(message):
    text = (message or '').lower()
    return 'test deployment' in text and 'pending' in text


def is_auto_patch_extracted_request(message):
    text = (message or '').lower()
    return 'auto patch' in text and 'extracted' in text


def is_vc_shift_request(message):
    text = (message or '').lower()
    return (
        'vc shift' in text
        or 'shift plan' in text
        or 'reviewer plan' in text
        or 'today reviewer' in text
        or 'who is reviewer' in text
        or 'shift' in text
        or 'review' in text
    )
