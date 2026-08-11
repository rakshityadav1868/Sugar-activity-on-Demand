# Copyright (C) 2026 Sugar Labs
#
# SPDX-License-Identifier: GPL-3.0-or-later

import unittest
from unittest import mock

from preview.runner import _add_toolbar_button
from preview.runner import _mark_sugar_preview_root
from preview.runner import _set_adjustment_bounds
from preview.runner import _try_exec_preview
from preview.runner import _remove_preview_sources


class TestAodPreviewCompatibility(unittest.TestCase):

    def test_preview_cleanup_removes_every_tracked_glib_source(self):
        instance = mock.Mock()
        instance._preview_glib_source_ids = [11, 22, 33]

        with mock.patch('preview.runner.GLib.source_remove') as remove:
            _remove_preview_sources(instance)

        self.assertEqual([], instance._preview_glib_source_ids)
        self.assertCountEqual(
            [mock.call(11), mock.call(22), mock.call(33)],
            remove.call_args_list)

    def test_toolbar_alias_inserts_at_end(self):
        toolbar_box = mock.Mock()
        item = object()

        _add_toolbar_button(toolbar_box, item)

        toolbar_box.toolbar.insert.assert_called_once_with(item, -1)

    def test_adjustment_alias_sets_both_limits(self):
        adjustment = mock.Mock()

        _set_adjustment_bounds(adjustment, 2, 12)

        adjustment.set_lower.assert_called_once_with(2)
        adjustment.set_upper.assert_called_once_with(12)

    def test_preview_widgets_receive_scoped_sugar_classes(self):
        widget = mock.Mock()

        _mark_sugar_preview_root(widget, 'aod-sugar-preview-canvas')

        context = widget.get_style_context.return_value
        context.add_class.assert_has_calls([
            mock.call('aod-sugar-preview-root'),
            mock.call('aod-sugar-preview-canvas'),
        ])

    def test_preview_supplies_gettext_fallback(self):
        class FakePreviewActivity:
            def __init__(self, handle=None, bundle_path=''):
                self.canvas = object()

            def get_canvas(self):
                return self.canvas

            def get_toolbar_box(self):
                return None

        source = (
            'class GeneratedActivity(PreviewActivity):\n'
            '    def __init__(self, handle=None):\n'
            '        PreviewActivity.__init__(self, handle)\n'
            '        self.label = _("Hello")\n'
        )

        with mock.patch(
                'preview.runner.PreviewActivity',
                FakePreviewActivity):
            result = _try_exec_preview(
                source, 'activity.py', '.', 'Preview')

        self.assertIsNotNone(result[0])
        self.assertEqual('Hello', result[0].label)


if __name__ == '__main__':
    unittest.main()
