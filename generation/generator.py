# Copyright (C) 2026 Sugar Labs
#
# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import dataclass
import ast
import hashlib
import json
import os
import re

from sugar3.activity import bundlebuilder

from core.licenses import get_license
from generation.templates import render_activity_source


@dataclass
class GenerationResult:
    spec: object
    plan: dict
    project_path: str
    bundle_path: str
    bundle_id: str
    files: dict
    provider: str = 'local'
    model: str = ''


def restore_generation_result(spec, summary):
    """Restore a completed result from its persisted artifact paths."""
    if not isinstance(summary, dict):
        return None

    project_path = summary.get('project_path', '')
    bundle_path = summary.get('bundle_path', '')
    if not os.path.isdir(project_path):
        return None
    if bundle_path and not os.path.isfile(bundle_path):
        bundle_path = ''

    plan_path = os.path.join(project_path, 'aod_plan.json')
    try:
        with open(plan_path, encoding='utf-8') as plan_file:
            plan = json.load(plan_file)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(plan, dict):
        return None

    bundle_id = summary.get('bundle_id') or plan.get('bundle_id', '')
    if not bundle_id:
        return None

    try:
        files = read_project_files(project_path)
    except OSError:
        return None

    return GenerationResult(
        spec=spec.normalized(),
        plan=plan,
        project_path=project_path,
        bundle_path=bundle_path,
        bundle_id=bundle_id,
        files=files,
        provider=summary.get('provider', plan.get('provider', 'local')),
        model=summary.get('model', plan.get('model', '')),
    )


def create_prototype_activity(spec, output_root, plan=None,
                              package_bundle=True, activity_source=None):
    """Generate a complete Sugar activity project and optional XO bundle."""
    errors = spec.validate()
    if errors:
        raise ValueError('\n'.join(errors))

    spec = spec.normalized()
    generation_plan = plan or build_plan(spec)
    generation_plan = enrich_plan(spec, generation_plan)
    project_path = assemble_project(
        spec,
        generation_plan,
        output_root,
        activity_source=activity_source,
    )

    bundle_path = ''
    if package_bundle:
        bundle_path = package_project(project_path)

    files = read_project_files(project_path)
    return GenerationResult(
        spec=spec,
        plan=generation_plan,
        project_path=project_path,
        bundle_path=bundle_path,
        bundle_id=generation_plan['bundle_id'],
        files=files,
    )


def infer_template(spec):
    if spec.template != 'auto':
        return spec.template

    prompt = spec.prompt.lower()
    words = set(re.findall(r'[a-z0-9]+', spec.prompt.lower()))
    scores = {
        'canvas': _keyword_score(words, {
            'art', 'canvas', 'comic', 'color', 'design', 'diagram', 'draw',
            'drawing', 'map', 'paint', 'picture', 'poster', 'shape', 'sketch',
            'atlas', 'city', 'compass', 'continent', 'country', 'discover',
            'earth', 'explore', 'explorer', 'geography', 'globe', 'landmark',
            'location', 'maps', 'navigate', 'navigation', 'ocean', 'osm',
            'openstreetmap', 'place', 'places', 'region', 'route', 'street',
            'streetmap', 'territory', 'travel', 'visualize', 'world',
        }) + _phrase_score(prompt, (
            'drawing canvas', 'paint a picture', 'open street map',
            'street map', 'world map', 'map of', 'mind map',
            'city map', 'explore the world', 'explore countries',
            'explore the map', 'geography activity', 'visualize',
        )),
        'narrative': _keyword_score(words, {
            'book', 'diary', 'journal', 'letter', 'narrative', 'poem',
            'reading', 'reflection', 'script', 'story', 'write', 'writing',
        }),
        'quiz': _keyword_score(words, {
            'answer', 'assessment', 'flashcard', 'practice', 'question',
            'quiz', 'spelling', 'test', 'trivia', 'vocabulary',
        }),
        'grid': _keyword_score(words, {
            'avoid', 'board', 'classify', 'collect', 'dodge', 'game', 'grid',
            'logic', 'match', 'maze', 'obstacle', 'obstacles', 'pattern',
            'player', 'puzzle', 'race', 'racer', 'racing', 'sort', 'table',
            'tile',
        }) + _phrase_score(prompt, ('board game', 'pattern game')),
        'utility': _keyword_score(words, {
            'calculate', 'calculator', 'checklist', 'converter', 'counter',
            'list', 'measure', 'organize', 'planner', 'schedule', 'stopwatch',
            'tally', 'timer', 'tool', 'tracker', 'utility',
        }) + _phrase_score(prompt, ('word count', 'word counting')),
    }
    scores['carrom'] = _carrom_template_score(prompt, words)
    scores['chess'] = _chess_template_score(prompt, words)

    template_order = _category_template_order(spec.category)
    ranked = sorted(
        scores.items(),
        key=lambda item: (
            item[1],
            -template_order.index(item[0])
            if item[0] in template_order else -len(template_order),
        ),
        reverse=True,
    )
    template, score = ranked[0]
    if score:
        return template

    # A discovery-category card is not a product specification. When the idea
    # has no family keyword, choose a neutral interactive surface instead of
    # silently turning it into a quiz, lesson, or writing exercise.
    return 'canvas'


def build_plan(spec):
    template = infer_template(spec)
    subject = _subject_from_spec(spec)
    learner_goal = spec.learner_goal or (
        'Use and complete the requested %s experience.' % spec.name.lower()
    )
    digest = hashlib.sha256(
        ('%s\0%s' % (spec.name, spec.prompt)).encode('utf-8')
    ).hexdigest()[:10]
    class_stem = _identifier(spec.name)
    bundle_id = 'org.sugarlabs.aod.%s%s' % (class_stem, digest)

    plan = {
        'name': spec.name,
        'summary': _summary_from_prompt(spec.prompt),
        'template': template,
        'category': spec.category,
        'categories': list(spec.learning_categories()),
        'age_band': spec.age_band,
        'learner_goal': learner_goal,
        'learner_steps': [
            'Start the activity.',
            'Use the main controls to play or create.',
            'Finish the activity and save progress to the Journal.',
        ],
        'ui_regions': [
            'Sugar activity toolbar for primary actions',
            'Dominant learner work canvas',
            'Compact persistent status only when the task requires it',
        ],
        'toolbar_actions': ['Activity', 'Reset or new', 'Stop'],
        'palette_actions': [
            'Place secondary settings in a Sugar palette or sub-toolbar',
        ],
        'feedback_model': (
            'Show selection and results directly in the work area; use Sugar '
            'alerts for important confirmation or completion feedback.'
        ),
        'color_model': (
            'Use grayscale Sugar chrome and theme colors; reserve bright '
            'colors for learner content and XO colors for learner ownership.'
        ),
        'responsive_behavior': (
            'Keep the work canvas dominant and allocation-driven at 1024x768 '
            'and 1200x900; compact or move secondary controls into palettes.'
        ),
        'word_bank': _word_bank(spec.prompt),
        'bundle_id': bundle_id,
        'class_name': 'GeneratedActivity',
        'license': spec.license_id,
        # A stable version that starts at 1 and is bumped only when the source
        # actually changes (see refine_activity), so reopening an unchanged
        # activity does not mint a new version the way a timestamp did.
        'activity_version': 1,
    }

    if template == 'quiz':
        plan['questions'] = _quiz_questions(spec)
    elif template == 'chess':
        plan['summary'] = (
            'A two-player chess board with legal moves, turns, captures, '
            'and Journal saving.'
        )
        plan['learner_goal'] = (
            'Play a complete legal chess game with another player.'
        )
        plan['learner_steps'] = [
            'Choose a white piece and make a legal move.',
            'Let black answer with a legal move.',
            'Continue until the game ends or the players stop.',
            'Reset or save the board when the game is done.',
        ]
        plan['word_bank'] = [
            'king', 'queen', 'rook', 'bishop', 'knight', 'pawn',
            'capture', 'check',
        ]
    elif template == 'carrom':
        plan['summary'] = (
            'A two-student carrom board for taking turns, aiming the '
            'striker, scoring pocketed coins, tracking fouls, and saving '
            'the match.'
        )
        plan['learner_goal'] = (
            'Play a complete two-player carrom match with scoring.'
        )
        plan['learner_steps'] = [
            'Student A chooses an aim point and takes the shot.',
            'Record pocketed coins, queen claims, or fouls.',
            'Switch turns so Student B plans the next shot.',
            'Compare scores and reset or save the match in the Journal.',
        ]
        plan['word_bank'] = [
            'striker', 'coin', 'queen', 'pocket', 'rebound', 'foul',
            'turn', 'score',
        ]
    elif template == 'canvas':
        plan['summary'] = 'A drawing canvas for creating %s.' % subject
        plan['learner_goal'] = 'Create the requested drawing or design.'
        plan['learner_steps'] = [
            'Choose a drawing tool and color.',
            'Draw directly on the canvas.',
            'Undo, revise, or clear the work as needed.',
            'Save the finished work to the Journal.',
        ]
    elif template == 'grid':
        plan['summary'] = (
            'An interactive grid-based activity for %s.' % subject
        )
        plan['learner_goal'] = (
            'Play or complete the requested grid-based activity.'
        )
        plan['learner_steps'] = [
            'Start or reset the activity.',
            'Use the controls to move, choose, or place items.',
            'Continue until the completion or win rule is reached.',
            'Save progress to the Journal.',
        ]
    elif template == 'narrative':
        plan['starter_text'] = (
            '%s\n\nStart creating here.\n' % spec.prompt.strip()
        )
    elif template == 'utility':
        plan['utility_mode'] = _utility_mode(spec.prompt)
        plan['summary'] = _utility_summary(subject, plan['utility_mode'])
        plan['learner_goal'] = _utility_goal(subject, plan['utility_mode'])
        plan['learner_steps'] = _utility_steps(plan['utility_mode'])
    return plan


def enrich_plan(spec, plan, references=None):
    """Normalize provider detail without inventing classroom requirements."""
    enriched = normalize_plan(spec, plan)
    references = references or ()
    template = enriched['template']
    subject = _subject_from_spec(spec)

    enriched['features'] = _unique_strings(
        enriched.get('features') or _activity_first_features(spec, enriched),
        8,
    )
    # Preserve explicitly planned instructional support, but do not synthesize
    # it for ordinary games, creative activities, or tools.
    enriched['classroom_flow'] = _unique_strings(
        enriched.get('classroom_flow') or [], 6)
    enriched['teacher_notes'] = _unique_strings(
        enriched.get('teacher_notes') or [], 6)
    enriched['assessment_prompts'] = _unique_strings(
        enriched.get('assessment_prompts') or [], 6)
    enriched['materials'] = _unique_strings(
        enriched.get('materials') or [], 6)
    if references:
        enriched['grounding_references'] = _unique_strings(
            [
                getattr(reference, 'title', '')
                for reference in references
                if getattr(reference, 'title', '')
            ],
            4,
        )
    elif 'grounding_references' not in enriched:
        enriched['grounding_references'] = []

    if template == 'quiz':
        enriched['questions'] = _enriched_quiz_questions(spec, enriched)
    elif template == 'chess':
        enriched['chess_show_move_log'] = _chess_should_show_move_log(
            spec,
            enriched,
        )
        if not enriched['chess_show_move_log']:
            enriched['features'] = _unique_strings([
                'clean chess board without move-history panel',
                'white and black turn taking',
                'legal move feedback',
                'Journal-saved board position',
            ] + enriched.get('features', []), 8)
            enriched['summary'] = (
                'A clean, two-player chess board without move-history clutter.'
            )
    elif template == 'carrom':
        enriched['players'] = _unique_strings(
            enriched.get('players') or ['Student A', 'Student B'],
            2,
        )
    elif template == 'narrative' and not enriched.get('starter_text'):
        enriched['starter_text'] = (
            '%s\n\nStart writing here.\n'
            % spec.prompt.strip()
        )
    elif template == 'utility':
        enriched['utility_mode'] = _utility_mode_from_plan(spec, enriched)

    if not enriched.get('summary') or enriched['summary'] == spec.prompt:
        enriched['summary'] = (
            'A Sugar activity for %s with working interactions and Journal '
            'saving.' % subject
        )

    return enriched


def _activity_first_features(spec, plan):
    """Return truthful fallbacks without turning a template into the idea.

    ``template`` is only the closest implementation/reference family.  A
    swimming game may use a canvas, for example, but that does not make it a
    drawing activity.  Provider plans normally supply concrete features; if
    they do not, keep the metadata honest and request-oriented instead of
    borrowing mechanics from the template renderer.
    """
    activity_kind = str(
        plan.get('activity_kind') or spec.name or 'requested activity'
    ).strip()
    interaction = str(plan.get('interaction_model') or '').strip()
    features = ['Core experience: %s' % activity_kind]
    if interaction:
        features.append('Interaction: %s' % interaction)
    features.extend([
        'Working request-specific controls with immediate visible feedback',
        'Clear progress, completion, and error states where applicable',
        'Journal-saved activity state and progress',
    ])
    return features


def normalize_plan(spec, plan):
    normalized = build_plan(spec)
    if not isinstance(plan, dict):
        return normalized

    text_fields = (
        'activity_kind',
        'color_model',
        'feedback_model',
        'interaction_model',
        'responsive_behavior',
        'summary',
        'learner_goal',
        'starter_text',
        'state_schema',
    )
    for field in text_fields:
        value = plan.get(field)
        if isinstance(value, str) and value.strip():
            normalized[field] = value.strip()

    template = plan.get('template')
    if template in (
            'canvas', 'carrom', 'chess', 'grid', 'narrative', 'quiz',
            'utility'):
        normalized['template'] = template

    steps = plan.get('learner_steps')
    if isinstance(steps, list):
        clean_steps = [
            str(step).strip() for step in steps
            if isinstance(step, str) and step.strip()
        ][:5]
        if clean_steps:
            normalized['learner_steps'] = clean_steps

    words = plan.get('word_bank')
    if isinstance(words, list):
        normalized['word_bank'] = _unique_strings(words, 8)

    for field, limit in (
            ('ui_regions', 8),
            ('toolbar_actions', 8),
            ('palette_actions', 8),
            ('features', 8),
            ('classroom_flow', 6),
            ('teacher_notes', 6),
            ('assessment_prompts', 6),
            ('materials', 6),
            ('grounding_references', 4)):
        values = plan.get(field)
        if isinstance(values, list):
            clean_values = _unique_strings(values, limit)
            if clean_values:
                normalized[field] = clean_values

    normalized['ui_regions'] = _sugar_native_ui_regions(
        normalized.get('ui_regions', []),
        preserve_reference_layout=(
            'Reference image brief' in (spec.prompt or '')),
    )

    questions = plan.get('questions')
    if isinstance(questions, list):
        clean_questions = []
        for item in questions[:10]:
            if not isinstance(item, dict):
                continue
            question = item.get('question', '')
            answer = item.get('answer', '')
            if isinstance(question, str) and question.strip():
                clean_questions.append({
                    'question': question.strip()[:240],
                    'answer': str(answer).strip()[:120] or 'anything',
                })
        if clean_questions:
            normalized['questions'] = clean_questions

    for field in (
            'provider',
            'model',
            'provider_fallback_reason',
            'code_source',
            'codegen_provider',
            'codegen_model',
            'codegen_fallback_reason',
            'repair_status',
            'original_source_hash',
            'parent_source_hash',
            'source_hash',
            'verification_status',
            'refine_method',
            'original_prompt',
            'enhanced_prompt',
            'runtime_check',
            'critic',
            'icon_source',
            'icon_svg'):
        value = plan.get(field)
        if isinstance(value, str) and value:
            normalized[field] = value

    if plan.get('bundle_id'):
        normalized['bundle_id'] = plan['bundle_id']

    for field in ('codegen_attempts', 'repair_attempts'):
        attempts = plan.get(field)
        if isinstance(attempts, int):
            normalized[field] = attempts

    # Preserve an explicit semantic version so a plan carried across
    # normalization (initial generation, refinement bump, reopen) keeps its
    # version instead of resetting to the build_plan default of 1.
    version = plan.get('activity_version')
    if isinstance(version, int) and not isinstance(version, bool) and \
            version > 0:
        normalized['activity_version'] = version

    repair_history = plan.get('repair_history')
    if isinstance(repair_history, list):
        # Repair events contain only JSON-safe diagnostic data assembled by
        # the pipeline.  Preserve a bounded history in aod_plan.json so a
        # successful activity remains explainable without bloating projects.
        clean_history = []
        for event in repair_history[-100:]:
            if not isinstance(event, dict):
                continue
            clean_event = dict(event)
            patches = clean_event.pop('patches', None)
            if isinstance(patches, list):
                clean_event.setdefault('patch_count', len(patches))
            clean_history.append(clean_event)
        normalized['repair_history'] = clean_history

    for field in (
            'chess_show_move_log',
            'reference_image_supplied',
            'reference_brief_supplied'):
        value = plan.get(field)
        if isinstance(value, bool):
            normalized[field] = value

    utility_mode = plan.get('utility_mode')
    if utility_mode in ('word_counter', 'counter', 'timer'):
        normalized['utility_mode'] = utility_mode

    return normalized


# Appended to every generated activity.py as a small layout safeguard.  It is
# deliberately non-visual: normal controls must be rendered by Sugar's GTK
# theme rather than a generated card/brand skin.  The traversal only prevents
# wrapped labels from claiming an unbounded natural width and starving the
# learner's dominant work canvas.
_AOD_AUTOSTYLE_MARKER = 'Sugar Activity Studio: native layout safeguards'

_AOD_AUTOSTYLE = '''

# --- Sugar Activity Studio: native layout safeguards ------------------------
def _aod_sugar_layout(widget, depth=0):
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk
    except Exception:
        return
    if widget is None or depth > 24:
        return
    try:
        if isinstance(widget, Gtk.Label):
            # A wrapped label with no width cap reports its full one-line
            # text width as its natural width, ballooning side panels far
            # past their requested size and starving the main work area.
            # Cap it so the layout keeps its intended proportions.
            if widget.get_line_wrap() and \
                    widget.get_max_width_chars() <= 0 and \
                    widget.get_width_chars() <= 0:
                widget.set_max_width_chars(34)
    except Exception:
        pass
    try:
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                _aod_sugar_layout(child, depth + 1)
    except Exception:
        pass


try:
    _aod_generated_init = GeneratedActivity.__init__

    def _aod_wrapped_init(self, *args, **kwargs):
        _aod_generated_init(self, *args, **kwargs)
        try:
            _aod_sugar_layout(self.get_canvas())
        except Exception:
            pass

    GeneratedActivity.__init__ = _aod_wrapped_init
except Exception:
    pass
'''


def assemble_project(spec, plan, output_root, activity_source=None):
    os.makedirs(output_root, exist_ok=True)
    project_path = _new_project_path(output_root, spec.name)
    activity_path = os.path.join(project_path, 'activity')
    os.makedirs(activity_path)
    os.makedirs(os.path.join(project_path, 'po'))

    license_info = get_license(spec.license_id)
    # Only None means "use the local renderer".  An empty or otherwise
    # invalid provider candidate must stay visible as a failure; silently
    # replacing it with a template would violate the repair-only contract.
    source = (render_activity_source(spec, plan)
              if activity_source is None else activity_source)
    # Add non-visual Sugar layout safeguards. Idempotent: a refined source
    # already carries the bootstrap, so never append it twice.
    if _AOD_AUTOSTYLE_MARKER in source:
        activity_py = source
    else:
        activity_py = source.rstrip() + '\n\n' + _AOD_AUTOSTYLE
    files = {
        'activity.py': activity_py,
        'setup.py': _SETUP_SOURCE,
        'README.md': _render_readme(spec, plan),
        'LICENSE': license_info.get_text(),
        'aod_plan.json': json.dumps(plan, indent=2, sort_keys=True) + '\n',
        os.path.join('activity', 'activity.info'):
            _render_activity_info(spec, plan),
        os.path.join('activity', 'activity.svg'): _activity_icon(plan),
        os.path.join('po', '%s.pot' % _identifier(spec.name)):
            _render_pot(spec, plan, _extract_translatable_strings(source)),
    }

    for relative_path, content in files.items():
        path = os.path.join(project_path, relative_path)
        with open(path, 'w', encoding='utf-8') as output:
            output.write(content)

    return project_path


def package_project(project_path):
    dist_path = os.path.join(project_path, 'dist')
    os.makedirs(dist_path, exist_ok=True)
    config = bundlebuilder.Config(
        source_dir=project_path,
        dist_dir=dist_path,
    )
    bundlebuilder.cmd_dist_xo(config, None)
    return os.path.join(dist_path, config.xo_name)


def read_project_files(project_path):
    result = {}
    for root, directories, filenames in os.walk(project_path):
        directories[:] = [
            name for name in directories if name not in ('dist', '__pycache__')
        ]
        for filename in filenames:
            path = os.path.join(root, filename)
            relative_path = os.path.relpath(path, project_path)
            try:
                with open(path, encoding='utf-8') as source:
                    result[relative_path] = source.read()
            except UnicodeDecodeError:
                continue
    return result


_SPDX_RE = re.compile(r'^(\s*#\s*SPDX-License-Identifier:).*$', re.MULTILINE)


def _replace_spdx_identifier(source, license_id):
    """Rewrite the first SPDX header line to the given license id."""
    def substitute(match):
        return '%s %s' % (match.group(1), license_id)

    updated, count = _SPDX_RE.subn(substitute, source, count=1)
    return updated if count else source


def apply_license_to_project(project_path, spec, plan):
    """Rewrite the on-disk license artifacts to match ``spec.license_id``.

    Generation bakes a default license into the project. When the learner
    chooses a license at install or export time we regenerate the LICENSE
    file, the ``activity.info`` license field, and the ``activity.py`` SPDX
    header so the packaged bundle carries the selected license. Returns the
    refreshed project file mapping.
    """
    license_info = get_license(spec.license_id)

    license_path = os.path.join(project_path, 'LICENSE')
    with open(license_path, 'w', encoding='utf-8') as license_file:
        license_file.write(license_info.get_text())

    info_path = os.path.join(project_path, 'activity', 'activity.info')
    with open(info_path, 'w', encoding='utf-8') as info_file:
        info_file.write(_render_activity_info(spec, plan))

    source_path = os.path.join(project_path, 'activity.py')
    try:
        with open(source_path, encoding='utf-8') as source_file:
            source = source_file.read()
    except OSError:
        source = ''
    if source:
        updated = _replace_spdx_identifier(source, spec.license_id)
        if updated != source:
            with open(source_path, 'w', encoding='utf-8') as source_file:
                source_file.write(updated)

    return read_project_files(project_path)


# Two-student board activities seat a partner; everything else is single-user.
_MULTI_PARTICIPANT_TEMPLATES = ('chess', 'carrom')

# Sugar's discovery field is `tags`; map the internal learning area to a
# reader-friendly tag so the Journal and home view surface the activity well.
_CATEGORY_TAGS = {
    'logic_math': 'Math',
    'science': 'Science',
    'language': 'Language',
    'games': 'Games',
    'creation': 'Art',
    'tools_utils': 'Tools',
}


def _max_participants(plan):
    if plan.get('template') in _MULTI_PARTICIPANT_TEMPLATES:
        return 2
    model = str(plan.get('interaction_model') or '').lower()
    if any(word in model for word in ('two', 'partner', 'multiplayer',
                                      'multi-player', 'multi player')):
        return 2
    return 1


def _activity_tags(spec, plan):
    """Build the Sugar `tags` discovery line from area + word bank."""
    tags = ['Education']
    label = _CATEGORY_TAGS.get(getattr(spec, 'category', ''))
    if label and label not in tags:
        tags.append(label)
    for word in plan.get('word_bank') or ():
        token = str(word).strip()
        if token.isalpha() and len(token) > 2:
            tag = token.capitalize()
            if tag not in tags:
                tags.append(tag)
        if len(tags) >= 6:
            break
    # Sugar parses the tags field on ';' (ActivityBundle._parse_info splits
    # on semicolons); a space join collapses everything into one bogus tag.
    return ';'.join(tags)


def _render_activity_info(spec, plan):
    return (
        '[Activity]\n'
        'name = %(name)s\n'
        'bundle_id = %(bundle_id)s\n'
        'icon = activity\n'
        'exec = sugar-activity3 activity.GeneratedActivity\n'
        'activity_version = %(activity_version)s\n'
        'license = %(license)s\n'
        'max_participants = %(max_participants)d\n'
        'summary = %(summary)s\n'
        'tags = %(tags)s\n'
    ) % {
        'name': spec.name.replace('\n', ' '),
        'bundle_id': plan['bundle_id'],
        'license': spec.license_id,
        'summary': plan['summary'].replace('\n', ' '),
        'activity_version': plan['activity_version'],
        'max_participants': _max_participants(plan),
        'tags': _activity_tags(spec, plan),
    }


def _extract_translatable_strings(source):
    """Return ordered, de-duplicated strings wrapped in _() in the source.

    The generated activity uses ``from gettext import gettext as _`` (see the
    codegen prompt), so every user-facing string is an ``_("...")`` call.  This
    harvests them for the translation template; unparseable source yields an
    empty (still valid) template.
    """
    strings = []
    seen = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return strings
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == '_'):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            value = first.value
            if value.strip() and value not in seen:
                seen.add(value)
                strings.append(value)
    return strings


def _po_escape(text):
    return (
        text.replace('\\', '\\\\')
            .replace('"', '\\"')
            .replace('\n', '\\n')
            .replace('\t', '\\t')
    )


def _render_pot(spec, plan, strings):
    """Render a gettext .pot translation template for the activity."""
    name = spec.name.replace('\n', ' ')
    header = (
        '# Translation template for %(name)s.\n'
        '# Copyright (C) 2026 Sugar Labs\n'
        '# This file is distributed under the same license as the '
        'activity.\n'
        '#\n'
        'msgid ""\n'
        'msgstr ""\n'
        '"Project-Id-Version: %(name)s %(version)s\\n"\n'
        '"Report-Msgid-Bugs-To: \\n"\n'
        '"MIME-Version: 1.0\\n"\n'
        '"Content-Type: text/plain; charset=UTF-8\\n"\n'
        '"Content-Transfer-Encoding: 8bit\\n"\n'
    ) % {'name': name, 'version': plan.get('activity_version', 1)}
    entries = ''.join(
        '\nmsgid "%s"\nmsgstr ""\n' % _po_escape(text)
        for text in strings
    )
    return header + entries


def _render_readme(spec, plan):
    steps = '\n'.join(
        '%d. %s' % (index, step)
        for index, step in enumerate(plan['learner_steps'], 1)
    )
    extra_sections = ''.join((
        _render_plan_section('Key features', plan.get('features')),
        _render_plan_section('Classroom flow', plan.get('classroom_flow')),
        _render_plan_section('Teacher notes', plan.get('teacher_notes')),
        _render_plan_section(
            'Assessment prompts',
            plan.get('assessment_prompts'),
        ),
        _render_plan_section('Materials', plan.get('materials')),
        _render_plan_section(
            'Grounded Sugar patterns',
            plan.get('grounding_references'),
        ),
    ))
    return (
        '# %(name)s\n\n'
        '%(summary)s\n\n'
        '## Activity goal\n\n'
        '%(goal)s\n\n'
        '## How to play or use it\n\n'
        '%(steps)s\n\n'
        '%(extra_sections)s'
        '## Generation details\n\n'
        '- Category: `%(category)s`\n'
        '- Template: `%(template)s`\n'
        '- Age band: `%(age_band)s`\n'
        '- License: `%(license)s`\n\n'
        'This project was generated by Sugar Activity on Demand and can be '
        'changed, tested, and shared as an XO bundle.\n'
    ) % {
        'name': spec.name,
        'summary': plan['summary'],
        'goal': plan['learner_goal'],
        'steps': steps,
        'extra_sections': extra_sections,
        'category': spec.category,
        'template': plan['template'],
        'age_band': spec.age_band,
        'license': spec.license_id,
    }


def _render_plan_section(title, values):
    if not values:
        return ''
    lines = [
        '- %s' % str(value).strip()
        for value in values
        if str(value).strip()
    ]
    if not lines:
        return ''
    return '## %s\n\n%s\n\n' % (title, '\n'.join(lines))


def _summary_from_prompt(prompt):
    summary = ' '.join(prompt.split())
    if len(summary) > 180:
        summary = summary[:177].rstrip() + '...'
    return summary


def _word_bank(prompt):
    words = re.findall(r'[A-Za-z][A-Za-z0-9-]+', prompt.lower())
    ignored = {
        'activity', 'and', 'build', 'create', 'for', 'make', 'that', 'the',
        'their', 'this', 'where', 'with',
    }
    return _unique_strings(
        [word for word in words if word not in ignored and len(word) > 2],
        8,
    )


def _keyword_score(words, keywords):
    return len(words.intersection(keywords))


def _phrase_score(prompt, phrases):
    return sum(1 for phrase in phrases if phrase in prompt)


def _category_template_order(category):
    orders = {
        'logic_math': [
            'quiz', 'grid', 'carrom', 'utility', 'canvas', 'narrative',
            'chess',
        ],
        'science': [
            'utility', 'canvas', 'grid', 'quiz', 'narrative', 'carrom',
            'chess',
        ],
        'language': [
            'narrative', 'quiz', 'canvas', 'grid', 'utility', 'carrom',
            'chess',
        ],
        'tools_utils': [
            'utility', 'grid', 'quiz', 'carrom', 'canvas', 'narrative',
            'chess',
        ],
        'games': [
            'carrom', 'chess', 'grid', 'quiz', 'canvas', 'narrative',
            'utility',
        ],
        'creation': [
            'canvas', 'narrative', 'grid', 'carrom', 'quiz', 'utility',
            'chess',
        ],
    }
    return orders.get(category, orders['logic_math'])


def _carrom_template_score(prompt, words):
    if 'carrom' in words:
        return 6
    if any(phrase in prompt for phrase in (
            'carrom board', 'carrom game', 'carrom activity')):
        return 6

    carrom_words = {
        'striker', 'pocket', 'pockets', 'coin', 'coins', 'queen',
        'rebound', 'flick', 'flicking', 'foul', 'fouls',
    }
    score = len(words.intersection(carrom_words))
    board_words = {'board', 'game', 'score', 'scoring', 'turn', 'turns'}
    if score and board_words & words:
        return 2 + score
    return 0


def _chess_template_score(prompt, words):
    if 'chess' in words or 'checkmate' in words:
        return 5
    if any(phrase in prompt for phrase in (
            'chess board', 'chess game', 'chess puzzle', 'legal chess')):
        return 5

    pieces = {
        'king', 'queen', 'rook', 'bishop', 'knight', 'pawn',
    }
    piece_count = len(words.intersection(pieces))
    chess_actions = {
        'capture', 'castle', 'castling', 'check', 'move', 'moves',
        'opening',
    }
    if piece_count >= 2 and words.intersection(chess_actions):
        return 3 + piece_count
    return 0


def _utility_mode(prompt):
    words = set(re.findall(r'[a-z0-9]+', prompt.lower()))
    text = prompt.lower()
    if 'word count' in text or 'word counting' in text or \
            ({'word', 'words'} & words and {'count', 'counting'} & words):
        return 'word_counter'
    if {'timer', 'stopwatch'} & words or 'time tracker' in text:
        return 'timer'
    if {'counter', 'count', 'tally', 'scorekeeper'} & words:
        return 'counter'
    return 'word_counter'


def _utility_mode_from_plan(spec, plan):
    mode = plan.get('utility_mode')
    if mode in ('word_counter', 'counter', 'timer'):
        return mode
    return _utility_mode(spec.prompt)


def _utility_summary(subject, mode):
    if mode == 'timer':
        return 'A timer for %s with start, pause, reset, and saving.' % subject
    if mode == 'counter':
        return (
            'A simple counter for tallying observations and decisions about '
            '%s.' % subject
        )
    return (
        'A text tool that counts words and characters for %s.' % subject
    )


def _utility_goal(subject, mode):
    if mode == 'timer':
        return 'Track elapsed time while working on %s.' % subject
    if mode == 'counter':
        return 'Track counts or scores connected to %s.' % subject
    return 'Measure and revise text connected to %s.' % subject


def _utility_steps(mode):
    if mode == 'timer':
        return [
            'Start the timer before the activity round.',
            'Pause when the round ends.',
            'Read or compare the elapsed time.',
            'Reset or save the timing note in the Journal.',
        ]
    if mode == 'counter':
        return [
            'Press plus when an event or idea appears.',
            'Press minus to correct a tally.',
            'Read or use the current count.',
            'Reset or save the tally in the Journal.',
        ]
    return [
        'Enter or paste the text to inspect.',
        'Read the word and character counts.',
        'Revise the text and compare the new count.',
        'Save the useful version to the Journal.',
    ]


def _unique_strings(values, limit):
    result = []
    seen = set()
    for value in values:
        text = str(value).strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) == limit:
            break
    return result


def _sugar_native_ui_regions(regions, preserve_reference_layout=False):
    """Collapse an explicitly two-sided/dashboard plan into Sugar regions.

    Generic provider plans sometimes describe a web dashboard literally as
    left controls + work area + right controls. Keeping that unchanged steers
    code generation away from Sugar composition. An analyzed reference image
    is different: its target-region placement is an explicit learner input and
    must remain intact, while host/browser regions have already been excluded
    by the vision brief. A single genuinely necessary panel also remains
    untouched.
    """
    if not isinstance(regions, list):
        return regions
    clean = _unique_strings(regions, 8)
    if preserve_reference_layout:
        return clean
    lowered = [value.lower() for value in clean]
    has_left = any(re.search(r'\bleft\b.*\b(sidebar|panel)\b|'
                             r'\b(sidebar|panel)\b.*\bleft\b', value)
                   for value in lowered)
    has_right = any(re.search(r'\bright\b.*\b(sidebar|panel)\b|'
                              r'\b(sidebar|panel)\b.*\bright\b', value)
                    for value in lowered)
    dashboard_regions = sum(
        bool(re.search(r'\b(sidebar|dashboard)\b', value))
        for value in lowered)
    if not ((has_left and has_right) or dashboard_regions >= 2):
        return clean

    preserved = '; '.join(clean)
    if len(preserved) > 900:
        preserved = preserved[:897].rstrip() + '...'
    return [
        'Dominant expanding learner creation or play workspace',
        ('Sugar ToolbarBox plus contextual palettes/sub-toolbars preserving '
         'the functions proposed in the original regions: %s' % preserved),
        ('At most one compact persistent tray or panel, and only for status '
         'or controls that must remain visible throughout the task'),
    ]


def _quiz_questions(spec):
    subject = spec.name.lower()
    return [
        {
            'question': 'What is the main idea in %s?' % subject,
            'answer': 'anything',
        },
        {
            'question': 'Explain one example in your own words.',
            'answer': 'anything',
        },
        {
            'question': 'What would you test or change next?',
            'answer': 'anything',
        },
    ]


def _enriched_quiz_questions(spec, plan):
    questions = plan.get('questions') or []
    if len(questions) >= 4:
        return questions[:6]

    subject = _subject_from_spec(spec)
    words = plan.get('word_bank') or []
    focus = words[0] if words else subject
    generated = questions[:]
    generated.extend([
        {
            'question': 'What is one important idea about %s?' % subject,
            'answer': 'anything',
        },
        {
            'question': 'Give an example that uses %s.' % focus,
            'answer': 'anything',
        },
        {
            'question': 'How would you explain %s to a partner?' % subject,
            'answer': 'anything',
        },
        {
            'question': 'What would you change or test next?',
            'answer': 'anything',
        },
    ])

    cleaned = []
    seen = set()
    for item in generated:
        question = item.get('question', '').strip()
        if not question or question.lower() in seen:
            continue
        seen.add(question.lower())
        cleaned.append({
            'question': question,
            'answer': str(item.get('answer', 'anything')).strip() or
            'anything',
        })
        if len(cleaned) == 6:
            break
    return cleaned


def _chess_should_show_move_log(spec, plan):
    if isinstance(plan.get('chess_show_move_log'), bool):
        return plan['chess_show_move_log']

    prompt = spec.prompt.lower()
    remove_words = (
        'remove', 'hide', 'without', 'no ', 'don\'t', 'do not', 'disable',
        'clean',
    )
    tracking_words = (
        'move log', 'move history', 'history', 'tracking', 'track moves',
        'move tracking', 'move list', 'log panel',
    )
    if any(word in prompt for word in remove_words) and \
            any(word in prompt for word in tracking_words):
        return False
    return True


def _subject_from_spec(spec):
    words = _word_bank(spec.prompt)
    if words:
        return ' '.join(words[:3])
    return spec.name.lower()


def _identifier(name):
    words = re.findall(r'[A-Za-z0-9]+', name)
    identifier = ''.join(word.capitalize() for word in words)
    if not identifier:
        identifier = 'Generated'
    if identifier[0].isdigit():
        identifier = 'Activity' + identifier
    return identifier[:45]


def _new_project_path(output_root, name):
    base = _identifier(name) + '.activity'
    candidate = os.path.join(output_root, base)
    suffix = 2
    while os.path.exists(candidate):
        candidate = os.path.join(
            output_root,
            '%s%d.activity' % (_identifier(name), suffix),
        )
        suffix += 1
    os.makedirs(candidate)
    return candidate


_SETUP_SOURCE = """#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later

from sugar3.activity import bundlebuilder


if __name__ == '__main__':
    bundlebuilder.start()
"""


def _activity_icon(plan):
    """Per-activity icon: the model's own drawing when the plan
    carries one, else the deterministic glyph, else the checkmark."""
    try:
        from generation.icons import recolor_icon_svg
        stroke = plan.get('icon_stroke_color', '#282828')
        fill = plan.get('icon_fill_color', '#FFFFFF')
        icon_svg = plan.get('icon_svg')
        if icon_svg:
            from generation.icons import sanitize_icon_svg
            safe = sanitize_icon_svg(icon_svg)
            if safe:
                colored = recolor_icon_svg(safe, stroke, fill)
                if colored:
                    return colored
        from generation.icons import render_activity_icon
        fallback = render_activity_icon(plan)
        return recolor_icon_svg(fallback, stroke, fill) or fallback
    except Exception:
        return _ACTIVITY_ICON


_ACTIVITY_ICON = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     width="55" height="55" viewBox="0 0 55 55">
  <circle cx="27.5" cy="27.5" r="21"
          fill="#ffffff" stroke="#000000" stroke-width="3.5"/>
  <path d="M17 29 L24 36 L39 19"
        fill="none" stroke="#000000" stroke-width="4"
        stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""
