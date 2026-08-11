# Copyright (C) 2026 Sugar Labs
#
# SPDX-License-Identifier: GPL-3.0-or-later

import unittest

from generation.prompts import build_system_prompt
from generation.prompts import extract_json_object
from core.spec import ActivitySpec


class TestAodPrompts(unittest.TestCase):

    def test_system_prompt_contains_sugar_constraints(self):
        spec = ActivitySpec(
            'Story Studio',
            'Create a story writing activity.',
            'creation',
            'MIT',
            template='narrative',
        )
        prompt = build_system_prompt(spec)
        self.assertIn('Subclass sugar3.activity.activity.Activity', prompt)
        self.assertIn('Return one JSON object', prompt)
        self.assertIn('canvas, carrom, chess, grid', prompt)
        self.assertIn('provider code generator owns', prompt)
        self.assertIn('not templates to copy', prompt)
        self.assertIn('Sugar whole-interface design contract', prompt)
        self.assertIn('toolbar_actions', prompt)
        self.assertIn('palette_actions', prompt)
        self.assertIn('responsive_behavior', prompt)
        self.assertIn('Do not plan a generic card grid', prompt)
        self.assertIn('Preserve every visible functional', prompt)
        self.assertIn('relative left/center/right placement', prompt)
        self.assertIn('reference decisions inside the target activity second',
                      prompt)
        self.assertIn('Do not omit, merge, move, or redesign', prompt)
        self.assertNotIn('local generator owns Python source', prompt)
        self.assertIn('large editable area', prompt)
        self.assertIn('Sugar Artwork', prompt)
        self.assertIn('Never substitute emoji', prompt)
        self.assertIn('stretched text button', prompt)
        self.assertIn('Journal restore must redraw', prompt)

    def test_system_prompt_supports_science_and_language(self):
        for category, expected in (
                ('science', 'experiments'),
                ('language', 'vocabulary')):
            spec = ActivitySpec(
                'Explore', 'Measure plant growth.', category, 'MIT')
            self.assertIn(expected, build_system_prompt(spec))

    def test_system_prompt_treats_learning_areas_as_discovery_hints(self):
        spec = ActivitySpec(
            'Eco Game', 'Build an ecosystem challenge.', 'science', 'MIT',
            categories=('science', 'games'))
        prompt = build_system_prompt(spec)
        self.assertIn('retrieve useful examples', prompt)
        self.assertIn('clear play loop', prompt)
        self.assertIn('discovery/RAG hints, not feature requirements', prompt)
        self.assertIn('Never inject math problems', prompt)

    def test_system_prompt_falls_back_on_unknown_category(self):
        spec = ActivitySpec(
            'Explore', 'Measure plant growth.', 'mystery', 'MIT')
        self.assertIn('learner-owned', build_system_prompt(spec))

    def test_extracts_fenced_json(self):
        value = extract_json_object(
            '```json\n{"template": "quiz", "summary": "Test"}\n```'
        )
        self.assertEqual('quiz', value['template'])

    def test_rejects_non_object_json(self):
        with self.assertRaises(ValueError):
            extract_json_object('["not", "an", "object"]')
