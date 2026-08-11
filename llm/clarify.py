# Copyright (C) 2026 Sugar Labs
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Guided generation: clarifying questions and a discussable build plan.

Before generating an activity, the Studio can ask the learner a few
tailored questions and then propose a plain-language plan to review and
adjust.  Both steps are optional refinements — like prompt enhancement,
any failure here degrades to skipping the step so generation is never
blocked.  The questions and the agreed plan are folded back into the
activity prompt, which already drives both planning and code generation.
"""

import logging
import re

from core.spec import AGREED_PLAN_HEADER
from core.spec import CONFIRMED_REQUIREMENTS_HEADER
from core.spec import MAX_PROMPT_LENGTH
from generation.prompts import extract_json_object

_QUESTIONS_TIMEOUT = 90
_PLAN_TIMEOUT = 120

_MIN_QUESTIONS = 0
_MAX_QUESTIONS = 4
_MAX_OPTIONS = 6
_QUESTION_TYPES = ('single', 'multi', 'text')

_MIN_USEFUL_PLAN = 20


def build_questions_system_prompt():
    return (
        'You are Sugar Activity on Demand, a friendly clarification '
        'assistant.\n'
        "Given the learner's complete request context, ask only unresolved "
        'questions that would materially change what gets built. The context '
        'may include a Reference image brief produced by a vision model.\n'
        'Return a SINGLE JSON object and nothing else:\n'
        '{\n'
        '  "questions": [\n'
        '    {"id": "short_slug", "label": "The question text?", '
        '"type": "single|multi|text", '
        '"options": ["Option A", "Option B"]}\n'
        '  ]\n'
        '}\n'
        'Rules:\n'
        '- Between %d and %d questions, ordered most important first.\n'
        '- Zero questions is the correct result when the request and '
        'reference already determine the important choices. Return '
        '{"questions": []} in that case.\n'
        '- Before adding each question, verify that its answer is absent from '
        'the learner request AND every reference-image section. Treat visible '
        'layout, controls, labels, palette, hierarchy, interaction cues, and '
        'Visible decisions as facts that are already answered.\n'
        '- Reference Uncertainties are possible question topics, not a quota. '
        'Ask about one only when resolving it would materially change scope, '
        'behavior, or the learning experience.\n'
        '- Never ask the learner to repeat, confirm, choose, or describe '
        'something already shown in the reference. Never ask a generic '
        'catch-all question.\n'
        '- "single": pick one option. "multi": pick several. "text": free '
        'text (omit options).\n'
        '- Give 2 to %d concise options for single/multi questions.\n'
        '- Questions must be specific to THIS idea, not generic.\n'
        '- Learning-area cards are optional discovery tags. Do not ask how '
        'to add math, language, science, assessment, reflection, or classroom '
        'content unless the learner idea already requests that content.\n'
        '- No markdown, no comments, no prose outside the JSON object.'
        % (_MIN_QUESTIONS, _MAX_QUESTIONS, _MAX_OPTIONS)
    )


def build_questions_user_prompt(spec):
    return (
        'Complete learner request context (including any analyzed reference '
        'facts):\n%s\n'
        'Optional discovery tags (do not inject as features): %s\n'
        'Age band: %s'
        % (spec.prompt, ', '.join(spec.learning_categories()), spec.age_band)
    )


def build_plan_system_prompt():
    return (
        'You are Sugar Activity on Demand, a friendly planning assistant '
        'for Sugar (GTK3) learning activities.\n'
        "Treat the learner's idea as the product definition. Learning-area "
        'cards are discovery hints only. Do not add math, vocabulary, quiz, '
        'assessment, reflection, explanation, classroom, or partner mechanics '
        'unless the idea, confirmed answers, or reference image asks for them. '
        'A race must remain a race; a drawing request must remain a drawing '
        'activity; a utility request must remain that utility.\n'
        'Propose a short, concrete build plan the learner can approve or '
        'adjust before you generate the activity.\n'
        'Cover, as short lines or bullets:\n'
        '- What we will build (one sentence)\n'
        '- Main screens / controls\n'
        '- Key features\n'
        '- How the learner uses it, and the win/completion rule\n'
        '- What gets saved to the Journal\n'
        'Keep it under 180 words. Plain text or light markdown bullets '
        'only — no code, no headings larger than a bullet, no preamble '
        'like "Here is".\n'
        "End with exactly: \"Reply with any changes, or say 'build it'.\""
    )


def build_plan_user_prompt(spec, answers_text, discussion=''):
    parts = [
        'Activity name: %s' % spec.name,
        'Learner idea: %s' % spec.prompt,
        'Optional discovery tags (do not inject as features): %s' % ', '.join(
            spec.learning_categories()),
        'Age band: %s' % spec.age_band,
    ]
    if answers_text:
        parts.append('\n%s' % answers_text)
    if discussion:
        parts.append(
            '\nOngoing discussion (revise the plan to reflect the latest '
            'request):\n%s' % discussion)
    return '\n'.join(parts)


def generate_questions(provider, spec, timeout=_QUESTIONS_TIMEOUT):
    """Return a list of normalized question dicts — fail-soft, never raises.

    On any failure (no provider, bad JSON, exception) returns an empty
    list, so the caller simply skips the questions step.
    """
    if provider is None or spec is None:
        return []

    try:
        response = provider.generate_plan(
            build_questions_system_prompt(),
            build_questions_user_prompt(spec),
            timeout=timeout,
        )
    except Exception as error:
        logging.warning('Clarifying questions failed: %s',
                        _redact_provider_value(error, provider))
        return []

    if isinstance(response, dict):
        payload = response
    else:
        try:
            payload = extract_json_object(response)
        except ValueError as error:
            logging.warning('Clarifying questions were not JSON: %s', error)
            return []

    return _normalize_questions(payload.get('questions'))


def generate_plan_proposal(provider, spec, answers_text='', discussion='',
                           timeout=_PLAN_TIMEOUT, stream_callback=None):
    """Return a plain-text build plan — fail-soft, never raises.

    On failure, returns a minimal plan synthesized from the spec so the
    guided flow can still advance to generation.
    """
    if provider is not None:
        try:
            response = provider.generate_text(
                build_plan_system_prompt(),
                build_plan_user_prompt(spec, answers_text, discussion),
                timeout=timeout,
                stream_callback=stream_callback,
            )
        except Exception as error:
            logging.warning('Plan proposal failed: %s',
                            _redact_provider_value(error, provider))
            response = None

        if isinstance(response, str) and \
                not _contains_provider_secret(response, provider):
            cleaned = _clean(response)
            if len(cleaned) >= _MIN_USEFUL_PLAN:
                return cleaned

    return _fallback_plan(spec, answers_text)


def format_answers(questions, answers):
    """Render collected answers as a compact requirements block.

    ``questions`` is the normalized list from :func:`generate_questions`;
    ``answers`` maps question id to a string (single/text) or a list of
    strings (multi).  Blank answers are skipped.
    """
    if not questions or not isinstance(answers, dict):
        return ''

    lines = []
    for question in questions:
        raw = answers.get(question.get('id'))
        value = _answer_text(raw)
        if not value:
            continue
        label = str(question.get('label', '')).strip() or question.get('id')
        lines.append('- %s: %s' % (label, value))

    if not lines:
        return ''
    return '%s\n%s' % (CONFIRMED_REQUIREMENTS_HEADER, '\n'.join(lines))


def build_activity_prompt(base_prompt, answers_text='', plan_text=''):
    """Fold guided context in without truncating the reference brief.

    The base prompt can contain the analyzed reference contract.  Reserve its
    complete bounded text first; supplemental answers/plan are useful, but
    must never push the reference off the end of MAX_PROMPT_LENGTH.
    """
    base = (base_prompt or '').strip()[:MAX_PROMPT_LENGTH]
    supplemental = []
    if answers_text:
        supplemental.append(answers_text.strip())
    if plan_text:
        supplemental.append('%s\n%s' % (
            AGREED_PLAN_HEADER, plan_text.strip()))
    extra = '\n\n'.join(section for section in supplemental if section)
    if not extra:
        return base
    separator = '\n\n'
    available = max(0, MAX_PROMPT_LENGTH - len(base) - len(separator))
    if available <= 0:
        return base
    if len(extra) > available:
        extra = extra[:available].rstrip()
    return '%s%s%s' % (extra, separator, base)


def _normalize_questions(raw_questions):
    if not isinstance(raw_questions, list):
        return []

    questions = []
    used_ids = set()
    for index, item in enumerate(raw_questions):
        if not isinstance(item, dict):
            continue
        label = str(item.get('label', '')).strip()
        if not label:
            continue

        qtype = str(item.get('type', 'text')).strip().lower()
        if qtype not in _QUESTION_TYPES:
            qtype = 'text'

        options = []
        if qtype in ('single', 'multi'):
            for option in item.get('options', []) or []:
                text = str(option).strip()
                if text and text not in options:
                    options.append(text)
                if len(options) >= _MAX_OPTIONS:
                    break
            if len(options) < 2:
                # Not enough choices to be a useful selection question.
                qtype = 'text'
                options = []

        qid = _slug(item.get('id') or label) or 'q%d' % index
        while qid in used_ids:
            qid = '%s_%d' % (qid, index)
        used_ids.add(qid)

        entry = {'id': qid, 'label': label, 'type': qtype}
        if options:
            entry['options'] = options
        questions.append(entry)
        if len(questions) >= _MAX_QUESTIONS:
            break

    if len(questions) < _MIN_QUESTIONS:
        return []
    return questions


def _answer_text(raw):
    if isinstance(raw, (list, tuple)):
        parts = [str(item).strip() for item in raw if str(item).strip()]
        return ', '.join(parts)
    if raw is None:
        return ''
    return str(raw).strip()


def _fallback_plan(spec, answers_text=''):
    lines = [
        'Build "%s": %s' % (spec.name, spec.prompt.strip()),
        '- Main screen with the core interaction and clear controls.',
        '- Toolbar with the standard Sugar activity buttons.',
        '- Progress is saved to and restored from the Journal.',
    ]
    if answers_text:
        lines.append('')
        lines.append(answers_text)
    lines.append("Reply with any changes, or say 'build it'.")
    return '\n'.join(lines)


def _slug(value):
    text = re.sub(r'[^a-z0-9]+', '_', str(value).strip().lower())
    return text.strip('_')[:40]


def _clean(text):
    if not isinstance(text, str):
        return ''
    cleaned = text.strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```[a-zA-Z0-9_-]*\n?', '', cleaned)
        if cleaned.endswith('```'):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    if len(cleaned) > MAX_PROMPT_LENGTH:
        cleaned = cleaned[:MAX_PROMPT_LENGTH].rstrip()
    return cleaned


def _provider_secrets(provider):
    return [
        value for value in (
            getattr(provider, '_api_key', ''),
            getattr(provider, 'api_key', ''),
        )
        if isinstance(value, str) and value
    ]


def _contains_provider_secret(value, provider):
    return isinstance(value, str) and any(
        secret in value for secret in _provider_secrets(provider))


def _redact_provider_value(value, provider):
    text = str(value)
    for secret in _provider_secrets(provider):
        text = text.replace(secret, '[redacted]')
    return text
