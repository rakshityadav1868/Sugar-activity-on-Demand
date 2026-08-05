# Copyright (C) 2026 Sugar Labs
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Top-level window hosting the Activity Studio panel."""

from gettext import gettext as _
import logging
import os

import gi
gi.require_version('Gtk', '3.0')

from gi.repository import Gdk  # noqa: E402
from gi.repository import Gtk  # noqa: E402

from ui.panel import CreateAIActivityPanel  # noqa: E402

# The launcher copy, not activity/activity.svg: this one is drawn outside
# Sugar, where nothing substitutes the stroke/fill entities.
_LAUNCHER_ICON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'sugar-aod-studio.svg')


class AODStudioWindow(Gtk.Window):
    """A plain desktop window around the create/studio panel."""

    def __init__(self):
        Gtk.Window.__init__(self, title=_('Sugar Activity Studio'))
        self.set_position(Gtk.WindowPosition.CENTER)
        self._set_window_icon()
        self._set_default_geometry()

        self.panel = CreateAIActivityPanel()
        self.add(self.panel)
        self.panel.show()
        self.panel.reset_view()

        self.panel.connect('close-requested', self.__close_requested_cb)
        self.connect('delete-event', self.__delete_event_cb)

    def _set_window_icon(self):
        """Draw the studio's own icon in the dock, alt-tab and titlebar."""
        if not os.path.isfile(_LAUNCHER_ICON):
            return
        try:
            Gtk.Window.set_default_icon_from_file(_LAUNCHER_ICON)
        except Exception:
            logging.debug('Could not set the window icon', exc_info=True)

    def _set_default_geometry(self):
        width, height = 1280, 860
        try:
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or \
                display.get_monitor(0)
            workarea = monitor.get_workarea()
            width = max(width, int(workarea.width * 0.9))
            height = max(height, int(workarea.height * 0.9))
        except Exception:
            logging.debug('Could not size window from monitor workarea',
                          exc_info=True)
        self.set_default_size(width, height)

    def __close_requested_cb(self, panel):
        self.close()

    def __delete_event_cb(self, window, event):
        try:
            self.panel.cancel_generation()
        except Exception:
            logging.exception('Could not cancel generation on close')
        return False
