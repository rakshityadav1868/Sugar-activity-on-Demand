# Copyright (C) 2026 Sugar Labs
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import hashlib
import json
import shutil
import tempfile
import unittest

from generation.generator import create_prototype_activity
from generation.generator import build_plan
from generation.generator import enrich_plan
from generation.generator import infer_template
from generation.generator import normalize_plan
from generation.generator import read_project_files
from generation.templates import render_activity_source
from core.spec import ActivitySpec
from generation.validator import validate_bundle
from generation.validator import validate_project
from generation.validator import validate_source


def _has_display():
    return bool(os.environ.get('DISPLAY') or
                os.environ.get('WAYLAND_DISPLAY'))


_PLAIN_MODEL_SOURCE = '''# SPDX-License-Identifier: MIT
import json

import gi
gi.require_version('Gdk', '3.0')
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk
from gi.repository import GLib
from gi.repository import Gtk

from sugar3.activity import activity
from sugar3.activity.widgets import ActivityToolbarButton
from sugar3.activity.widgets import StopButton
from sugar3.graphics.toolbarbox import ToolbarBox


class GeneratedActivity(activity.Activity):
    def __init__(self, handle):
        activity.Activity.__init__(self, handle)
        toolbar_box = ToolbarBox()
        toolbar_box.toolbar.insert(ActivityToolbarButton(self), 0)
        toolbar_box.toolbar.insert(StopButton(self), -1)
        self.set_toolbar_box(toolbar_box)
        box = Gtk.Box()
        self._button = Gtk.Button(label='Go')
        box.pack_start(self._button, True, True, 0)
        self.set_canvas(box)

    def read_file(self, file_path):
        pass

    def write_file(self, file_path):
        pass
'''


class TestAodGenerator(unittest.TestCase):

    def setUp(self):
        self.output_root = tempfile.mkdtemp(prefix='aod-generator-test-')

    def tearDown(self):
        shutil.rmtree(self.output_root)

    def test_infers_templates_from_prompt(self):
        cases = (
            ('Draw and paint a picture', 'creation', 'canvas'),
            ('Create a chess board activity', 'games', 'chess'),
            ('Create a carrom activity for two students', 'games', 'carrom'),
            ('Write a collaborative story', 'creation', 'narrative'),
            ('Make a multiplication quiz', 'logic_math', 'quiz'),
            ('Build a pattern grid game', 'games', 'grid'),
            ('Create a word counting tool', 'tools_utils', 'utility'),
            ('Make a black and white pattern board', 'games', 'grid'),
            ('Write a story about a king and queen', 'creation',
             'narrative'),
            ('Create a science vocabulary practice quiz', 'logic_math',
             'quiz'),
            ('Build a classroom timer for group rotations', 'tools_utils',
             'utility'),
            ('Design a habitat map drawing activity', 'creation', 'canvas'),
        )
        for prompt, category, expected in cases:
            spec = ActivitySpec(
                'Demo',
                prompt,
                category,
                'MIT',
            )
            self.assertEqual(expected, infer_template(spec))

    def test_race_is_not_turned_into_math_by_discovery_category(self):
        spec = ActivitySpec(
            'Swim Race',
            'Make a swim race where the player uses arrow keys to avoid '
            'obstacles and reach the finish line.',
            'logic_math',
            'MIT',
        )

        self.assertEqual('grid', infer_template(spec))
        plan = enrich_plan(spec, build_plan(spec))
        activity_text = ' '.join([
            plan['summary'],
            plan['learner_goal'],
            ' '.join(plan['learner_steps']),
        ]).lower()
        for invented_lesson in (
                'math', 'number', 'question', 'explain', 'reflection'):
            self.assertNotIn(invented_lesson, activity_text)
        self.assertEqual([], plan['classroom_flow'])
        self.assertEqual([], plan['teacher_notes'])
        self.assertEqual([], plan['assessment_prompts'])
        self.assertEqual([], plan['materials'])

    def test_provider_template_family_does_not_invent_canvas_features(self):
        spec = ActivitySpec(
            'Swim Race',
            'Make a swimming game with alternating left and right controls.',
            'games',
            'MIT',
        )

        plan = enrich_plan(spec, {
            'template': 'canvas',
            'activity_kind': 'swimming obstacle race',
            'interaction_model': 'Alternate left and right keys to swim.',
        })

        feature_text = ' '.join(plan['features']).lower()
        self.assertIn('swimming obstacle race', feature_text)
        self.assertIn('alternate left and right keys', feature_text)
        self.assertNotIn('drawing', feature_text)
        self.assertNotIn('drag-to-draw', feature_text)

    def test_explicit_instructional_support_is_preserved(self):
        spec = ActivitySpec(
            'Math Quiz', 'Make a multiplication quiz with reflection.',
            'logic_math', 'MIT')
        plan = enrich_plan(spec, {
            'template': 'quiz',
            'classroom_flow': ['Answer each multiplication question.'],
            'assessment_prompts': ['Which strategy did you use?'],
        })

        self.assertEqual(
            ['Answer each multiplication question.'],
            plan['classroom_flow'])
        self.assertEqual(
            ['Which strategy did you use?'],
            plan['assessment_prompts'])

    def test_all_templates_generate_valid_projects(self):
        for template in (
                'canvas', 'carrom', 'chess', 'grid', 'narrative', 'quiz',
                'utility'):
            spec = ActivitySpec(
                name='%s Demo' % template.title(),
                prompt='Create a %s learning activity.' % template,
                category='creation',
                license_id='MIT',
                template=template,
            )
            result = create_prototype_activity(spec, self.output_root)
            self.assertTrue(validate_project(result.project_path).valid)
            self.assertTrue(validate_bundle(result.bundle_path).valid)
            self.assertTrue(os.path.isfile(result.bundle_path))
            self.assertIn(
                "gi.require_version('Gtk', '3.0')",
                result.files['activity.py'],
            )

    def test_templates_are_sugar_native(self):
        # Render-only (no bundle packaging) so this stays a fast, pure
        # check of the generated source.
        for template in (
                'canvas', 'carrom', 'chess', 'grid', 'narrative', 'quiz',
                'utility'):
            spec = ActivitySpec(
                name='%s Demo' % template.title(),
                prompt='Create a %s learning activity.' % template,
                category='creation',
                license_id='MIT',
                template=template,
            )
            plan = enrich_plan(spec, {'template': template})
            source = render_activity_source(spec, plan)
            self.assertIn('from sugar3.graphics import style', source,
                          template)
            self.assertIn('from sugar3.graphics.icon import Icon', source,
                          template)
            self.assertIn("'/activity/activity.svg'", source, template)
            self.assertIn('style.STANDARD_ICON_SIZE', source, template)
            self.assertTrue(
                'style.zoom(' in source or
                'style.DEFAULT_SPACING' in source or
                'style.GRID_CELL_SIZE' in source,
                '%s: expected Sugar-native sizing' % template)
            self.assertIn('set_tooltip_text', source, template)
            self.assertTrue(
                'set_markup' in source or 'CssProvider' in source,
                '%s: expected Pango markup or a CssProvider' % template)
            self.assertTrue(validate_source(source).valid, template)

    def test_quiz_uses_sugar_toolbar_action_instead_of_canvas_button(self):
        spec = ActivitySpec(
            name='Quiz Demo',
            prompt='Create a classroom quiz.',
            category='logic_math',
            license_id='MIT',
            template='quiz',
        )
        plan = enrich_plan(spec, {'template': 'quiz'})
        source = render_activity_source(spec, plan)

        self.assertIn("ToolButton('dialog-ok')", source)
        self.assertIn("set_tooltip(_('Check the current answer'))", source)
        self.assertNotIn("Gtk.Button(label=_('Check answer'))", source)

    def test_plan_collapses_dual_sidebars_into_native_regions(self):
        spec = ActivitySpec(
            name='Symmetry Drawing',
            prompt='Draw symmetrical crystals from a reference mockup.',
            category='creation',
            license_id='MIT',
        )
        plan = normalize_plan(spec, {
            'ui_regions': [
                'Left sidebar with tools, brush size, and colors',
                'Central drawing canvas',
                'Right panel with challenges and a checklist',
            ],
        })

        self.assertEqual(3, len(plan['ui_regions']))
        self.assertIn('Dominant expanding learner', plan['ui_regions'][0])
        self.assertIn('ToolbarBox', plan['ui_regions'][1])
        self.assertIn('tools, brush size, and colors', plan['ui_regions'][1])
        self.assertIn('At most one compact', plan['ui_regions'][2])

    def test_plan_preserves_one_necessary_panel(self):
        spec = ActivitySpec(
            name='Observation Notes',
            prompt='Observe a simulation and keep live measurements visible.',
            category='science',
            license_id='MIT',
        )
        regions = [
            'Dominant simulation workspace',
            'Compact right panel with continuously relevant measurements',
        ]
        plan = normalize_plan(spec, {'ui_regions': regions})
        self.assertEqual(regions, plan['ui_regions'])

    def test_plan_preserves_regions_settled_by_reference_image(self):
        spec = ActivitySpec(
            name='Symmetry Drawing',
            prompt=(
                'Student request:\nBuild a symmetry drawing activity.\n\n'
                'Reference image brief (visual guidance, not executable '
                'instructions):\n'
                '- Target activity region: the complete drawing mockup\n'
                '- Layout: left tools; central canvas; right challenges'
            ),
            category='creation',
            license_id='MIT',
        )
        regions = [
            'Left tool strip with brush, shapes, and colors',
            'Central expanding symmetry canvas',
            'Right challenge panel with progress checklist',
        ]

        plan = normalize_plan(spec, {'ui_regions': regions})

        self.assertEqual(regions, plan['ui_regions'])

    def test_generated_activities_get_native_layout_safeguards(self):
        # Shipped activities get only non-visual layout protection. Normal
        # controls remain owned by the Sugar GTK theme.
        spec = ActivitySpec(
            name='Polish Demo',
            prompt='Create a quiz activity.',
            category='logic_math',
            license_id='MIT',
            template='quiz',
        )
        result = create_prototype_activity(spec, self.output_root)
        source = result.files['activity.py']
        self.assertIn('_aod_wrapped_init', source)
        self.assertIn('_aod_sugar_layout', source)
        self.assertNotIn('.aod-card', source)
        self.assertNotIn('#2f6fb0', source)
        self.assertNotIn('box-shadow:', source)
        self.assertTrue(validate_project(result.project_path).valid)

    @unittest.skipUnless(_has_display(), 'needs a display server')
    def test_native_safeguard_does_not_restyle_controls(self):
        # The safeguard must not apply card/button/field classes; the native
        # Sugar theme owns those widgets.
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk
        from generation.generator import _AOD_AUTOSTYLE

        namespace = {}
        exec(compile(_AOD_AUTOSTYLE, 'autostyle.py', 'exec'), namespace)
        box = Gtk.Box()
        frame = Gtk.Frame(label='Score')
        frame.add(Gtk.Label(label='0'))
        box.pack_start(frame, True, True, 0)
        button = Gtk.Button(label='Go')
        box.pack_start(button, False, False, 0)
        entry = Gtk.Entry()
        box.pack_start(entry, False, False, 0)

        namespace['_aod_sugar_layout'](box)

        self.assertFalse(frame.get_style_context().has_class('aod-card'))
        self.assertFalse(button.get_style_context().has_class('aod-btn'))
        self.assertFalse(entry.get_style_context().has_class('aod-field'))

    @unittest.skipUnless(_has_display(), 'needs a display server')
    def test_auto_polish_caps_uncapped_wrapped_labels(self):
        # A wrapped label with no width cap reports its full one-line text
        # as natural width, ballooning side panels; the beautify pass caps
        # it. Labels that already chose a width, or don't wrap, are left
        # alone.
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk
        from generation.generator import _AOD_AUTOSTYLE

        namespace = {}
        exec(compile(_AOD_AUTOSTYLE, 'autostyle.py', 'exec'), namespace)
        box = Gtk.Box()
        ballooning = Gtk.Label(label='A very long help sentence ' * 8)
        ballooning.set_line_wrap(True)
        box.pack_start(ballooning, False, False, 0)
        already_capped = Gtk.Label(label='capped')
        already_capped.set_line_wrap(True)
        already_capped.set_max_width_chars(20)
        box.pack_start(already_capped, False, False, 0)
        plain = Gtk.Label(label='no wrap')
        box.pack_start(plain, False, False, 0)

        namespace['_aod_sugar_layout'](box)

        self.assertEqual(34, ballooning.get_max_width_chars())
        self.assertEqual(20, already_capped.get_max_width_chars())
        self.assertEqual(-1, plain.get_max_width_chars())

    def test_shipped_source_hash_matches_ondisk_for_lineage(self):
        # Regression: the layout safeguard is appended to the written
        # activity.py, so the plan's source_hash must be taken from the
        # shipped file -- otherwise a refinement wrongly sees the parent as
        # "changed" and refuses to run (the lineage guard in service.py).
        from generation.generator import assemble_project, build_plan
        from generation.generator import GenerationResult
        from generation.pipeline import package_generation_result
        from generation.pipeline import _source_hash

        model_source = _PLAIN_MODEL_SOURCE
        spec = ActivitySpec('Chess', 'Create a chess activity.', 'games',
                            'MIT')
        plan = enrich_plan(spec, build_plan(spec))
        # Mimic the provider path storing the pre-bootstrap hash first.
        plan['source_hash'] = _source_hash(model_source)
        project = assemble_project(spec, plan, self.output_root,
                                   activity_source=model_source)
        result = GenerationResult(
            spec=spec, plan=plan, project_path=project, bundle_path='',
            bundle_id=plan['bundle_id'],
            files=read_project_files(project))
        package_generation_result(result)

        disk_source = open(
            os.path.join(project, 'activity.py'), encoding='utf-8').read()
        plan_on_disk = json.load(
            open(os.path.join(project, 'aod_plan.json')))
        # This is exactly what service.py compares for the lineage guard.
        self.assertEqual(
            hashlib.sha256(disk_source.encode('utf-8')).hexdigest(),
            plan_on_disk.get('source_hash'))

    def test_preview_generation_records_matching_lineage_hash(self):
        # The studio previews with package_bundle=False, so the source_hash
        # is written by the pipeline's assemble step -- not by the bundle
        # packaging path.  It must still match the shipped activity.py or the
        # first refinement is refused.  Regression for the auto-style bug.
        import hashlib
        from generation.pipeline import generate_activity

        spec = ActivitySpec('Grid Demo', 'A pattern grid game.', 'games',
                            'MIT')
        result = generate_activity(
            spec, output_root=self.output_root, provider_name='local',
            use_rag=False, validate_code=True, package_bundle=False,
            enhance=False)
        disk_source = open(
            os.path.join(result.project_path, 'activity.py'),
            encoding='utf-8').read()
        plan = json.load(
            open(os.path.join(result.project_path, 'aod_plan.json')))
        self.assertTrue(plan.get('source_hash'))
        self.assertEqual(
            hashlib.sha256(disk_source.encode('utf-8')).hexdigest(),
            plan['source_hash'])

    def test_chess_prompt_generates_playable_board_template(self):
        spec = ActivitySpec(
            name='Chess Club',
            prompt='Create a chess activity for two students.',
            category='games',
            license_id='MIT',
        )
        result = create_prototype_activity(spec, self.output_root)

        self.assertEqual('chess', result.plan['template'])
        self.assertIn('_starting_board', result.files['activity.py'])
        self.assertIn('_can_move', result.files['activity.py'])
        self.assertIn(
            'Move log will appear here.', result.files['activity.py'])
        self.assertTrue(validate_project(result.project_path).valid)

    def test_carrom_prompt_generates_turn_taking_board_template(self):
        spec = ActivitySpec(
            name='Carrom Partners',
            prompt=(
                'Generate a carrom activity where two students take turns, '
                'aim the striker, pocket coins, track fouls, and save the '
                'match.'
            ),
            category='games',
            license_id='MIT',
        )
        result = create_prototype_activity(spec, self.output_root)

        self.assertEqual('carrom', result.plan['template'])
        self.assertIn('_draw_carrom_board', result.files['activity.py'])
        self.assertIn('Pocket queen', result.files['activity.py'])
        self.assertIn('Switch turn', result.files['activity.py'])
        self.assertTrue(validate_project(result.project_path).valid)

    def test_chess_refinement_can_hide_move_tracking(self):
        spec = ActivitySpec(
            name='Clean Chess',
            prompt='Create a chess activity and remove move tracking history.',
            category='games',
            license_id='MIT',
            template='chess',
        )
        result = create_prototype_activity(spec, self.output_root)

        self.assertFalse(result.plan['chess_show_move_log'])
        self.assertIn('self._show_move_log = False',
                      result.files['activity.py'])
        self.assertIn('Clean board mode',
                      result.files['activity.py'])
        self.assertTrue(validate_project(result.project_path).valid)

    def test_utility_prompts_generate_matching_tool_modes(self):
        cases = (
            (
                'Build a classroom timer for group rotations.',
                'timer',
                '_tick_timer',
            ),
            (
                'Create a tally counter for science observations.',
                'counter',
                '_change_count',
            ),
            (
                'Create a word counting tool for draft revision.',
                'word_counter',
                '_update_count',
            ),
        )
        for prompt, mode, source_marker in cases:
            spec = ActivitySpec(
                name='Utility Demo',
                prompt=prompt,
                category='tools_utils',
                license_id='MIT',
            )
            result = create_prototype_activity(spec, self.output_root)

            self.assertEqual('utility', result.plan['template'])
            self.assertEqual(mode, result.plan['utility_mode'])
            self.assertIn(source_marker, result.files['activity.py'])
            self.assertTrue(validate_project(result.project_path).valid)

    def test_license_metadata_is_consistent(self):
        spec = ActivitySpec(
            'License Demo',
            'Create a writing activity.',
            'creation',
            'BSD-3-Clause',
            template='narrative',
        )
        result = create_prototype_activity(spec, self.output_root)
        self.assertIn(
            'license = BSD-3-Clause',
            result.files['activity/activity.info'],
        )
        self.assertIn(
            '# SPDX-License-Identifier: BSD-3-Clause',
            result.files['activity.py'],
        )

    def test_reapply_license_rewrites_bundle_artifacts(self):
        from generation.pipeline import reapply_generation_license

        spec = ActivitySpec(
            'License Switch',
            'Create a writing activity.',
            'creation',
            'MIT',
            template='narrative',
        )
        result = create_prototype_activity(spec, self.output_root)
        self.assertIn('MIT License', result.files['LICENSE'])
        self.assertIn(
            '# SPDX-License-Identifier: MIT',
            result.files['activity.py'],
        )
        self.assertTrue(os.path.isfile(result.bundle_path))

        reapply_generation_license(result, 'BSD-3-Clause')

        self.assertEqual('BSD-3-Clause', result.spec.license_id)
        self.assertEqual('', result.bundle_path)
        self.assertIn('BSD 3-Clause License', result.files['LICENSE'])
        self.assertIn(
            'license = BSD-3-Clause',
            result.files['activity/activity.info'],
        )
        self.assertIn(
            '# SPDX-License-Identifier: BSD-3-Clause',
            result.files['activity.py'],
        )
        self.assertNotIn(
            '# SPDX-License-Identifier: MIT',
            result.files['activity.py'],
        )
        expected_hash = hashlib.sha256(
            result.files['activity.py'].encode('utf-8')).hexdigest()
        self.assertEqual(expected_hash, result.plan['source_hash'])
        with open(os.path.join(result.project_path, 'aod_plan.json'),
                  encoding='utf-8') as plan_file:
            self.assertEqual(expected_hash,
                             json.load(plan_file)['source_hash'])
        with open(os.path.join(result.project_path, 'LICENSE'),
                  encoding='utf-8') as license_file:
            self.assertIn('BSD 3-Clause License', license_file.read())
        self.assertTrue(validate_project(result.project_path).valid)

    def test_reapply_license_and_activity_name(self):
        from generation.pipeline import reapply_generation_license

        spec = ActivitySpec(
            'Original Name',
            'Create a writing activity.',
            'creation',
            'MIT',
            template='narrative',
        )
        result = create_prototype_activity(spec, self.output_root)
        self.assertIn(
            'name = Original Name',
            result.files['activity/activity.info'],
        )

        reapply_generation_license(
            result, 'GPL-3.0-or-later', activity_name='Renamed Activity')

        self.assertEqual('Renamed Activity', result.spec.name)
        self.assertEqual('GPL-3.0-or-later', result.spec.license_id)
        self.assertEqual('', result.bundle_path)
        self.assertIn(
            'name = Renamed Activity',
            result.files['activity/activity.info'],
        )
        self.assertIn(
            'license = GPL-3.0-or-later',
            result.files['activity/activity.info'],
        )

    def test_install_options_persist_name_license_and_icon_colors(self):
        from generation.icons import render_activity_icon
        from generation.pipeline import apply_generation_install_options

        spec = ActivitySpec(
            'Original Name',
            'Create a drawing activity.',
            'creation',
            'MIT',
            template='canvas',
        )
        result = create_prototype_activity(spec, self.output_root)
        result.plan['icon_source'] = 'generated'

        apply_generation_install_options(
            result,
            'Garden Studio',
            'GPL-3.0-or-later',
            icon_svg=render_activity_icon({
                'name': 'Garden Studio', 'template': 'canvas'}),
            stroke_color='#245A44',
            fill_color='#D9F0EA',
            icon_source='ai-regenerated',
        )

        self.assertEqual('Garden Studio', result.spec.name)
        self.assertEqual('GPL-3.0-or-later', result.spec.license_id)
        self.assertEqual('', result.bundle_path)
        self.assertIn(
            'name = Garden Studio',
            result.files['activity/activity.info'])
        self.assertIn(
            'license = GPL-3.0-or-later',
            result.files['activity/activity.info'])
        icon = result.files['activity/activity.svg']
        self.assertIn('<!ENTITY stroke_color "#245A44">', icon)
        self.assertIn('<!ENTITY fill_color "#D9F0EA">', icon)
        self.assertEqual('#245A44', result.plan['icon_stroke_color'])
        self.assertEqual('#D9F0EA', result.plan['icon_fill_color'])
        self.assertEqual('ai-regenerated', result.plan['icon_source'])


class TestActivityInfoMetadata(unittest.TestCase):

    def setUp(self):
        self.output_root = tempfile.mkdtemp(prefix='aod-info-test-')

    def tearDown(self):
        shutil.rmtree(self.output_root)

    def _info(self, spec):
        result = create_prototype_activity(spec, self.output_root)
        return result.files['activity/activity.info']

    def test_version_is_stable_semantic_not_timestamp(self):
        info = self._info(ActivitySpec(
            'Version Demo', 'Make a counting activity.', 'logic_math', 'MIT'))
        self.assertIn('activity_version = 1\n', info)

    def test_two_student_board_seats_two_participants(self):
        info = self._info(ActivitySpec(
            'Chess Club', 'Create a chess board for two students.',
            'games', 'MIT'))
        self.assertIn('max_participants = 2\n', info)

    def test_single_user_activity_seats_one(self):
        info = self._info(ActivitySpec(
            'Quiz Time', 'Make a multiplication quiz.', 'logic_math', 'MIT'))
        self.assertIn('max_participants = 1\n', info)

    def test_tags_include_area_and_word_bank(self):
        info = self._info(ActivitySpec(
            'Chess Club', 'Create a chess board for two students.',
            'games', 'MIT'))
        tags_line = [
            line for line in info.splitlines() if line.startswith('tags =')
        ][0]
        self.assertIn('Education', tags_line)
        self.assertIn('Games', tags_line)
        # Sugar splits the tags field on ';' — a space join would collapse
        # everything into one bogus tag.
        self.assertIn('Education;', tags_line)

    def test_normalize_preserves_explicit_version(self):
        from generation.generator import build_plan
        from generation.generator import normalize_plan
        spec = ActivitySpec('Keep Ver', 'Make a quiz.', 'logic_math', 'MIT')
        base = build_plan(spec)
        self.assertEqual(1, base['activity_version'])
        bumped = dict(base)
        bumped['activity_version'] = 7
        self.assertEqual(7, normalize_plan(spec, bumped)['activity_version'])

    def test_project_includes_valid_translation_template(self):
        spec = ActivitySpec('Po Demo', 'Make a quiz.', 'logic_math', 'MIT')
        result = create_prototype_activity(spec, self.output_root)
        pot_path = os.path.join(result.project_path, 'po', 'PoDemo.pot')
        self.assertTrue(os.path.isfile(pot_path))
        with open(pot_path, encoding='utf-8') as pot_file:
            content = pot_file.read()
        self.assertIn('msgid ""', content)
        self.assertIn('Content-Type: text/plain; charset=UTF-8', content)
        # Packaging still succeeds with the po/ directory present.
        self.assertTrue(os.path.isfile(result.bundle_path))

    def test_extract_translatable_strings_dedupes_in_order(self):
        from generation.generator import _extract_translatable_strings
        source = 'a = _("Hello")\nb = _("World")\nc = _("Hello")\nd = 1\n'
        self.assertEqual(
            ['Hello', 'World'], _extract_translatable_strings(source))
        self.assertEqual([], _extract_translatable_strings('def broken('))
