# Copyright (C) 2026 Sugar Labs
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""SEARCH/REPLACE refinement for generated Sugar activities.

Instead of regenerating the entire activity.py on every refinement,
this module asks the model to return only the changed regions as
SEARCH/REPLACE blocks.  This cuts output tokens from ~12k to ~1k per
refinement, making classroom iteration 10-20x cheaper and faster.

If any SEARCH block does not match the current source (the model
hallucinated a line, or the change is too large for a small diff), the
caller rejects that patch transaction and asks for a corrected patch.  The
existing source is never discarded in favour of full regeneration.
"""

import ast
import difflib
import re


SEARCH_MARKER = '<<<<<<< SEARCH'
DIVIDER_MARKER = '======='
REPLACE_MARKER = '>>>>>>> REPLACE'


def build_refine_system_prompt():
    """System prompt for SEARCH/REPLACE refinement.

    This is a separate prompt from the codegen system prompt because
    the task is fundamentally different: the model is editing existing
    code, not writing new code from scratch.
    """
    return (
        'You are Sugar Activity on Demand, editing an existing Sugar '
        'activity.\n\n'
        'The user will give you the current activity.py and a refinement '
        'request.  Return ONLY the changes as SEARCH/REPLACE blocks in '
        'this exact format:\n\n'
        '<<<<<<< SEARCH\n'
        '<exact lines from the current source to find>\n'
        '=======\n'
        '<replacement lines>\n'
        '>>>>>>> REPLACE\n\n'
        'Rules:\n'
        '- The SEARCH section must be copied EXACTLY from the current '
        'source, including indentation and whitespace.  Do not paraphrase '
        'or reformat.\n'
        '- The REPLACE section is the new code that replaces the SEARCH '
        'section.\n'
        '- Treat the new refinement request as an acceptance checklist. '
        'Implement every clause that can affect behavior or appearance; do '
        'not satisfy a request with comments, renamed labels, or metadata.\n'
        '- A refinement must cause a visible or functional improvement in '
        'the running activity. When the request says improve, fix, or add '
        'features without much detail, inspect the existing code, identify '
        'the weakest relevant behavior, and make concrete learner-visible '
        'changes while preserving working parts.\n'
        '- Diagnose question-shaped feedback as a repair request. For '
        'example, "why does the character disappear?" means find and fix '
        'the state/update/drawing defect, not add an explanation label.\n'
        '- For game mechanics, update the complete behavior: initialized '
        'state, input handlers, update/collision logic, drawing/HUD, reset '
        'or restart behavior, and Journal persistence where applicable.\n'
        '- You may output multiple SEARCH/REPLACE blocks.  Separate them '
        'with a blank line.\n'
        '- Keep each SEARCH block as small as possible while still being '
        'unique in the file.  3-10 lines is ideal.\n'
        '- Do NOT output the entire file.  Do NOT output anything except '
        'SEARCH/REPLACE blocks.  No explanations, no markdown, no code '
        'fences.\n'
        '- If the refinement requires adding new methods or large new '
        'sections, use a SEARCH block that matches an anchor line (like '
        'a method definition or the end of a class) and include the new '
        'code in the REPLACE section.\n'
        '- Never output FULLREGEN and never replace the whole file.  Express '
        'larger edits as several focused SEARCH/REPLACE blocks anchored to '
        'the existing source.\n'
        '- Preserve all Sugar Activity patterns: ToolbarBox, StopButton, '
        'set_canvas, read_file/write_file, Journal persistence.\n'
        '- Preserve the whole-interface Sugar design: dominant learner '
        'canvas, compact persistent panels only when necessary, primary '
        'actions in Sugar toolbars, contextual actions in palettes or '
        'sub-toolbars, Sugar style constants/widgets, grayscale chrome, and '
        'allocation-driven responsive layout. Never introduce a generic '
        'card dashboard, brand skin, or unnecessary permanent sidebar.\n'
        '- If the user message includes a reference image, inspect it '
        'directly and make the requested visual/layout change match it. Use '
        'any written Reference image brief as supporting context, not as a '
        'replacement for the attached pixels. Preserve every visible target '
        'region/control one-for-one, including approximate proportions, '
        'alignment, ordering, palette relationships, and state. Do not omit, '
        'merge, relocate, or redesign target activity content in the name of '
        'Sugar-native cleanup. Apply Sugar conventions only to host chrome '
        'and reference-unspecified details.\n'
        '- Keep the same class name GeneratedActivity.\n'
        '- Use only classroom-safe imports.  No networking, subprocesses, '
        'or filesystem access.\n'
    )


def build_refine_user_prompt(current_source, refinement_request,
                             plan_context=''):
    """User prompt for SEARCH/REPLACE refinement.

    Sends the full current source so the model can copy exact lines for
    SEARCH blocks.  The plan context is optional and kept small.
    """
    parts = [
        'NEW REFINEMENT REQUEST (implement every applicable clause):\n',
        refinement_request.strip(),
        '\n\n--- CURRENT ACTIVITY CONTEXT ---\n',
    ]
    if plan_context:
        parts.append('Current plan context:\n')
        parts.append(plan_context)
        parts.append('\n\n')
    parts.extend([
        'Current activity.py (%d lines):\n' % current_source.count('\n'),
        current_source.rstrip(),
        '\n\n--- END CURRENT ACTIVITY ---\n\n'
        'Re-check the new refinement request: ',
        refinement_request.strip(),
        '\n\n'
        'Return SEARCH/REPLACE blocks for the changes.  Copy SEARCH '
        'lines EXACTLY from the source above.  Never output FULLREGEN or a '
        'complete replacement file; split larger changes into focused '
        'blocks.'
    ])
    return ''.join(parts)


def validate_refinement_result(current_source, candidate_source, request):
    """Return concrete reasons a refinement did not fulfil its request.

    The normal validator proves that a candidate is a safe, runnable Sugar
    activity.  It cannot prove that an edit actually addressed the latest
    teacher message because the parent can already satisfy the broad activity
    prompt.  This lightweight delta gate checks only common, unambiguous
    refinement intents and leaves open-ended creative judgment to the model.
    """
    errors = []
    request_lower = (request or '').lower()
    if current_source == candidate_source:
        return ['The refinement did not change activity.py.']

    changed_lines = []
    matcher = difflib.SequenceMatcher(
        None, current_source.splitlines(), candidate_source.splitlines())
    for tag, _old_start, _old_end, new_start, new_end in matcher.get_opcodes():
        if tag in ('replace', 'insert'):
            changed_lines.extend(
                candidate_source.splitlines()[new_start:new_end])
    changed = '\n'.join(changed_lines).lower()

    # "Improve" and question-shaped bug reports must alter executable code,
    # not merely add reassuring comments or labels.
    asks_for_improvement = bool(re.search(
        r'\b(improve|improved|improvement|fix|broken|bug|not working|'
        r"doesn['’]?t work|why (?:is|does|do|can))\b",
        request_lower))
    if asks_for_improvement:
        try:
            before_ast = ast.dump(ast.parse(current_source), include_attributes=False)
            after_ast = ast.dump(ast.parse(candidate_source), include_attributes=False)
        except (SyntaxError, TypeError):
            # Static validation reports syntax failures with a better message.
            before_ast = after_ast = None
        if before_ast is not None and before_ast == after_ast:
            errors.append(
                'The refinement changed only comments or formatting; it must '
                'change the running activity.')

    if re.search(r'\b(graphic|graphics|visual|appearance|colour|color|'
                 r'animation|artwork)\b', request_lower):
        visual_signals = (
            'draw', 'cairo', 'set_source', 'rgb', 'gradient', 'paint',
            'arc(', 'rectangle(', 'line_to(', 'move_to(', 'particle',
            'queue_draw',
        )
        if not any(signal in changed for signal in visual_signals):
            errors.append(
                'The request asks for a visual improvement, but no drawing '
                'or rendering code changed.')

    if re.search(r'\b(life|lives|heart|hearts)\b', request_lower):
        candidate_lower = candidate_source.lower()
        if 'lives' not in candidate_lower and 'life' not in candidate_lower:
            errors.append(
                'The request asks for lives, but the activity has no lives '
                'state or display.')
        elif not any(signal in changed for signal in (
                'lives', 'life', 'heart', 'damage', 'collision')):
            errors.append(
                'The request asks to improve lives, but the lives mechanic '
                'was not changed.')

    if re.search(r'\b(arrow keys?|keyboard|keys? controls?|wasd)\b',
                 request_lower):
        candidate_lower = candidate_source.lower()
        if not any(signal in candidate_lower for signal in (
                'key-press-event', 'key_press_event', 'gdk.key_')):
            errors.append(
                'The request asks for keyboard control, but no keyboard '
                'input handler is present.')
        elif not any(signal in changed for signal in (
                'key_', 'key-', 'key_', 'keys_pressed', 'keys_held')):
            errors.append(
                'The request asks to improve keyboard control, but keyboard '
                'handling was not changed.')

    return errors


def parse_search_replace(response):
    """Parse SEARCH/REPLACE blocks from a model response.

    Returns a list of (search, replace) tuples, or None if a legacy/model
    response requested the forbidden FULLREGEN escape hatch.  Callers must
    reject that response rather than regenerating the file.  Raises
    ValueError when the response is malformed.
    """
    if not isinstance(response, str):
        raise ValueError('Refinement response must be text.')

    text = response.strip()

    if text.startswith('FULLREGEN') and SEARCH_MARKER not in text:
        return None

    blocks = []
    pos = 0
    _NL_DIVIDER = '\n' + DIVIDER_MARKER
    _NL_REPLACE = '\n' + REPLACE_MARKER

    while True:
        search_start = text.find(SEARCH_MARKER, pos)
        if search_start < 0:
            break

        after_search = search_start + len(SEARCH_MARKER)

        # Anchor to line boundary so '=======' inside source code is ignored.
        divider_nl = text.find(_NL_DIVIDER, after_search)
        if divider_nl < 0:
            raise ValueError(
                'Malformed SEARCH/REPLACE: missing ======= divider after '
                '<<<<<<< SEARCH at position %d.' % search_start
            )
        after_divider = divider_nl + len(_NL_DIVIDER)

        # Same anchoring for >>>>>>> REPLACE.
        replace_nl = text.find(_NL_REPLACE, after_divider)
        if replace_nl < 0:
            raise ValueError(
                'Malformed SEARCH/REPLACE: missing >>>>>>> REPLACE after '
                '======= divider at position %d.' % divider_nl
            )

        search_text = _strip_block_edges(text[after_search:divider_nl])
        replace_text = _strip_block_edges(text[after_divider:replace_nl])

        if not search_text:
            raise ValueError(
                'Malformed SEARCH/REPLACE: empty SEARCH section at '
                'position %d.' % search_start
            )

        blocks.append((search_text, replace_text))
        pos = replace_nl + len(_NL_REPLACE)

    if not blocks:
        if '<<<<<<<' not in text:
            raise ValueError(
                'Refinement response did not contain SEARCH/REPLACE '
                'blocks. Return focused exact patches; whole-file '
                'regeneration is forbidden.'
            )
        raise ValueError(
            'Refinement response contained SEARCH markers but no valid '
            'blocks were parsed.'
        )

    return blocks


def apply_patches(source, patches):
    """Apply SEARCH/REPLACE patches to source code.

    Returns (patched_source, applied_count, failed_count).  Each patch
    is a (search, replace) tuple.  Matching is whitespace-tolerant: we
    normalize trailing whitespace per line but preserve leading
    indentation.  If any patch fails to match, it is skipped and
    counted in failed_count.

    The caller should reject the entire patch transaction when failed_count
    is non-zero; it must not fall back to full regeneration.
    """
    lines = source.split('\n')
    applied = 0
    failed = 0

    for search_text, replace_text in patches:
        search_lines = search_text.split('\n')
        while search_lines and search_lines[-1] == '':
            search_lines.pop()
        if not search_lines:
            failed += 1
            continue

        match_index = _find_block(lines, search_lines)
        if match_index < 0:
            failed += 1
            continue

        replace_lines = replace_text.split('\n') if replace_text else []

        start = match_index
        end = match_index + len(search_lines)

        lines = lines[:start] + replace_lines + lines[end:]
        applied += 1

    patched = '\n'.join(lines)
    if not patched.endswith('\n'):
        patched += '\n'
    return patched, applied, failed


def _find_block(lines, search_lines):
    """Find the index of a search block in lines.

    Uses whitespace-tolerant matching: trailing whitespace is ignored,
    but leading indentation is preserved.  Returns the line index of
    the first match, or -1 if not found.
    """
    normalized_search = [_normalize_line(line) for line in search_lines]
    if not normalized_search:
        return -1

    normalized_lines = [_normalize_line(line) for line in lines]

    for start in range(len(normalized_lines) - len(normalized_search) + 1):
        if normalized_lines[start:start + len(normalized_search)] == \
                normalized_search:
            return start
    return -1


def _normalize_line(line):
    """Normalize a line for whitespace-tolerant matching.

    Strips trailing whitespace but preserves leading indentation.
    """
    return line.rstrip()


def _strip_block_edges(text):
    """Remove leading/trailing newlines from a block's content."""
    stripped = text.strip('\n')
    if stripped.startswith('```'):
        first_nl = stripped.find('\n')
        if first_nl >= 0:
            stripped = stripped[first_nl + 1:]
    if stripped.endswith('```'):
        last_fence = stripped.rfind('```')
        if last_fence >= 0:
            stripped = stripped[:last_fence]
    return stripped.rstrip('\n')
