# Copyright (C) 2026 Sugar Labs
#
# SPDX-License-Identifier: GPL-3.0-or-later

import unittest

from generation.known_repairs import apply_known_api_repairs
from generation.known_repairs import find_known_api_issues


class TestKnownApiRepairs(unittest.TestCase):

    def test_adds_missing_cairo_import_for_gradient_canvas(self):
        source = (
            'import gi\n'
            'def draw(cr):\n'
            '    gradient = cairo.LinearGradient(0, 0, 0, 100)\n'
            '    cr.set_source(gradient)\n'
        )

        issues = find_known_api_issues(source)
        repaired, repairs = apply_known_api_repairs(source)

        self.assertTrue(any('without importing cairo' in issue
                            for issue in issues))
        self.assertIn('import cairo\nimport gi\n', repaired)
        self.assertEqual(
            ['missing_cairo_import'],
            [repair['kind'] for repair in repairs])

    def test_keeps_existing_cairo_import(self):
        source = (
            'import cairo\n'
            'gradient = cairo.LinearGradient(0, 0, 0, 100)\n'
        )

        repaired, repairs = apply_known_api_repairs(source)

        self.assertEqual(source, repaired)
        self.assertEqual([], repairs)

    def test_repairs_nonexistent_cairo_begin_new_path(self):
        source = (
            'def draw(cr):\n'
            '    cr.begin_new_path()\n'
            '    cr.move_to(0, 0)\n'
        )

        issues = find_known_api_issues(source)
        repaired, repairs = apply_known_api_repairs(source)

        self.assertTrue(any('begin_new_path' in issue for issue in issues))
        self.assertIn('cr.new_path()', repaired)
        self.assertNotIn('begin_new_path', repaired)
        self.assertEqual(
            ['cairo_begin_new_path'],
            [repair['kind'] for repair in repairs])

    def test_rejects_attaching_an_ellipse_helper_to_cairo_context(self):
        source = (
            'def draw(cr):\n'
            '    cr.ellipse = lambda x, y: None\n'
            '    cr.ellipse(1, 2)\n'
        )

        issues = find_known_api_issues(source)

        self.assertTrue(any(
            'does not allow generated code to attach custom attributes'
            in issue for issue in issues), issues)

    def test_rejects_wrong_cairo_gradient_stop_arity(self):
        source = (
            'import cairo\n'
            'gradient = cairo.LinearGradient(0, 0, 100, 0)\n'
            'gradient.add_color_stop_rgba(0.0, 0.0, 1.0, 0.0)\n'
            'gradient.add_color_stop_rgb(1.0, 0.0, 1.0)\n'
        )

        issues = find_known_api_issues(source)

        self.assertTrue(any(
            'add_color_stop_rgba at line' in issue and
            'add_color_stop_rgb at line' in issue
            for issue in issues), issues)

    def test_accepts_correct_cairo_gradient_stop_arity(self):
        source = (
            'import cairo\n'
            'gradient = cairo.LinearGradient(0, 0, 100, 0)\n'
            'gradient.add_color_stop_rgba(0.0, 0.0, 1.0, 1.0, 0.0)\n'
            'gradient.add_color_stop_rgb(1.0, 0.0, 1.0, 1.0)\n'
        )

        issues = find_known_api_issues(source)

        self.assertFalse(any('gradient color stops' in issue
                             for issue in issues), issues)

    def test_removes_invalid_sugar_metadata_save(self):
        source = (
            'def write_file(self, file_path):\n'
            '    self.metadata["score"] = "10"\n'
            '    self.metadata.save()\n'
        )

        issues = find_known_api_issues(source)
        repaired, repairs = apply_known_api_repairs(source)

        self.assertTrue(any('metadata has no save() method' in issue
                            for issue in issues), issues)
        self.assertNotIn('metadata.save', repaired)
        self.assertIn('self.metadata["score"]', repaired)
        self.assertIn('sugar_metadata_save',
                      [repair['kind'] for repair in repairs])

    def test_repairs_failed_activity_api_spellings_only(self):
        source = (
            'self.sym_none = Gtk.RadioToolButton()\n'
            'self.sym_none.set_tooltip(_("No symmetry"))\n'
            'button = ToolButton(icon_name="edit-undo")\n'
            'button.set_tooltip(_("Undo"))\n'
            'panel.set_size_request(style.grid_size * 4, -1)\n'
        )

        repaired, repairs = apply_known_api_repairs(source)

        self.assertIn('self.sym_none.set_tooltip_text(', repaired)
        self.assertIn('button.set_tooltip(_("Undo"))', repaired)
        self.assertIn('style.GRID_CELL_SIZE * 4', repaired)
        self.assertEqual(
            {'raw_gtk_tooltip_method', 'sugar_style_constant'},
            {repair['kind'] for repair in repairs},
        )

    def test_does_not_rewrite_unrelated_tooltip_call(self):
        source = 'button.set_tooltip(_("Keep"))\n'

        repaired, repairs = apply_known_api_repairs(source)

        self.assertEqual(source, repaired)
        self.assertEqual([], repairs)

    def test_repairs_journal_exists_without_model_retry(self):
        source = (
            'import os, json\n'
            'from gi.repository import GLib\n\n'
            'def read_file(file_path):\n'
            '    if not os.path.exists(file_path):\n'
            '        return\n'
            '    return json.load(open(file_path))\n'
        )

        repaired, repairs = apply_known_api_repairs(source)

        self.assertNotIn('import os', repaired)
        self.assertIn('import json', repaired)
        self.assertIn(
            'if not GLib.file_test(file_path, GLib.FileTest.EXISTS):',
            repaired,
        )
        self.assertEqual(
            {'journal_file_exists', 'forbidden_os_import'},
            {repair['kind'] for repair in repairs},
        )

    def test_keeps_os_when_other_filesystem_usage_remains(self):
        source = (
            'import os\n'
            'exists = os.path.exists(file_path)\n'
            'os.remove(file_path)\n'
        )

        repaired, repairs = apply_known_api_repairs(source)

        self.assertEqual(source, repaired)
        self.assertEqual([], repairs)


if __name__ == '__main__':
    unittest.main()
