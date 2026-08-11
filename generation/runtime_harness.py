# Copyright (C) 2026 Sugar Labs
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Subprocess body for the generation runtime check.

Runs a generated activity the same way the studio preview does:
instantiate it against the PreviewActivity stubs, pump the GTK loop,
and exercise the generated class's own Journal round-trip.  Exits 0
and prints RUNTIME-OK on success; any crash prints the traceback and
exits nonzero so the parent can feed it back to the model.
"""

import logging
import os
import random
import sys
import tempfile
import traceback


class _StartupProblems(logging.Handler):
    """Collect WARNING+ records emitted while the activity starts.

    The preview runner deliberately degrades — salvaging a partial
    canvas after an __init__ crash, stubbing failed imports — so
    learners always see something.  The gate must not accept degraded
    code, and every degradation is logged as a warning, so warnings
    during startup are failures here.
    """

    def __init__(self):
        logging.Handler.__init__(self)
        self.setFormatter(logging.Formatter('%(message)s'))
        self.problems = []

    def emit(self, record):
        # Theme noise (missing stock icons etc.) also lands on the
        # root logger; every degradation message from the preview
        # runner mentions "preview", so key on that.
        if record.levelno >= logging.WARNING \
                and 'preview' in record.getMessage().lower():
            self.problems.append(self.format(record))


def main(project_dir):
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    from gi.repository import Gdk
    from gi.repository import GLib
    from gi.repository import Gtk

    from preview.runner import render_activity_preview

    # Generated games commonly use random entity types. Keep the acceptance
    # run reproducible so a broken delayed branch cannot pass or fail by luck.
    random.seed(0)
    problems = _StartupProblems()
    logging.getLogger().addHandler(problems)
    try:
        instance, canvas, toolbar_ = render_activity_preview(project_dir)
    finally:
        logging.getLogger().removeHandler(problems)
    if instance is None:
        sys.stderr.write('Activity failed to start: %s\n' % canvas)
        return 1
    if problems.problems:
        sys.stderr.write(
            'Activity started only in degraded mode:\n%s\n'
            % '\n\n'.join(problems.problems))
        return 1

    # Realize the returned canvas in an offscreen toplevel. Merely creating a
    # Gtk.DrawingArea does not dispatch its draw signal, which previously let
    # broken callbacks ship as a blank white activity. The Studio embeds the
    # same canvas in a visible container, so this is the faithful runtime gate.
    host = Gtk.OffscreenWindow()
    host.set_default_size(1024, 700)
    host.add(canvas)

    # PyGObject reports draw/timer callback exceptions through sys.excepthook
    # instead of propagating them out of Gtk.main_iteration_do(). Record the
    # hook before showing the host so even the very first draw is checked.
    spin_seconds = _env_float('AOD_RUNTIME_SPIN_SECONDS', 3.0)
    callback_failures = []
    previous_hook = sys.excepthook

    def _record_failure(exc_type, exc_value, exc_tb):
        callback_failures.append(''.join(
            traceback.format_exception(exc_type, exc_value, exc_tb)))

    sys.excepthook = _record_failure
    try:
        host.show_all()
        canvas.queue_draw()
        for _ in range(30):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            Gtk.main_iteration_do(False)
        # Exercise the most common game controls. This starts games that wait
        # for input and catches event-handler or delayed draw failures that a
        # passive first-frame check cannot reach. Non-game widgets simply
        # ignore these standard GTK key events.
        for keyval in (
                Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right,
                Gdk.KEY_space):
            press = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
            press.keyval = keyval
            release = Gdk.Event.new(Gdk.EventType.KEY_RELEASE)
            release.keyval = keyval
            canvas.emit('key-press-event', press)
            canvas.emit('key-release-event', release)
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
        _exercise_delayed_game_state(instance, canvas, Gtk, GLib)
        if spin_seconds > 0:
            loop = GLib.MainLoop()
            GLib.timeout_add(int(spin_seconds * 1000), loop.quit)
            loop.run()
    finally:
        sys.excepthook = previous_hook
    if callback_failures:
        sys.stderr.write(
            'Activity crashed inside an event callback:\n%s\n'
            % '\n'.join(callback_failures))
        host.destroy()
        return 1

    # Some generated games invoke write_file(None) only on victory or a
    # level transition. Exercise that same call when it exists in the source;
    # the ordinary Journal round-trip below uses a real path and cannot expose
    # a broken autosave-only branch such as self.metadata.save().
    if _source_calls_write_file_none(project_dir):
        instance.write_file(None)

    # The generated class overrides read_file/write_file; run them for
    # real so broken Journal persistence fails the gate.
    handle, journal_path = tempfile.mkstemp(prefix='aod-runtime-journal-')
    os.close(handle)
    try:
        instance.write_file(journal_path)
        instance.read_file(journal_path)
    finally:
        try:
            os.remove(journal_path)
        except OSError:
            pass

    try:
        instance.cleanup()
    except Exception:
        pass
    host.destroy()

    print('RUNTIME-OK')
    return 0


def _exercise_delayed_game_state(instance, canvas, Gtk, GLib):
    """Move common generated games near a level transition before spinning.

    A passive three-second run only covers startup. Scrolling games commonly
    reveal their finish gate, boss, or next-level drawing branches much later,
    which allowed callback crashes to ship. Advancing progress state keeps the
    gate fast while exercising those delayed branches deterministically.
    """
    try:
        configs = getattr(instance, 'level_configs', None)
        level = int(getattr(instance, 'current_level', 0))
        config = configs[level] if isinstance(configs, (list, tuple)) else None
        length = config.get('length') if isinstance(config, dict) else None
        if not isinstance(length, (int, float)) or length <= 0 or \
                not hasattr(instance, 'distance_traveled'):
            return
        instance.distance_traveled = max(0.0, float(length) - 200.0)
        if getattr(instance, 'game_state', None) == 'START':
            instance.game_state = 'PLAYING'
        canvas.queue_draw()
        for _ in range(30):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)

        # Exercise common late state-machine drawing branches immediately.
        # Unsupported state names are harmless; supported but incomplete
        # branches surface through the callback-failure hook above.
        original_state = getattr(instance, 'game_state', None)
        if isinstance(original_state, str):
            for state in ('CINEMATIC', 'DRAGON_FIGHT', 'VICTORY', 'GAMEOVER'):
                instance.game_state = state
                canvas.queue_draw()
                phase_loop = GLib.MainLoop()
                GLib.timeout_add(30, phase_loop.quit)
                phase_loop.run()
            instance.game_state = original_state
            canvas.queue_draw()
    except Exception:
        # Generated callbacks are captured by sys.excepthook. Introspection
        # itself is best-effort and must not fail non-game activities.
        return


def _env_float(name, default):
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _source_calls_write_file_none(project_dir):
    try:
        with open(os.path.join(project_dir, 'activity.py'),
                  encoding='utf-8') as source_file:
            return 'write_file(None)' in source_file.read()
    except OSError:
        return False


if __name__ == '__main__':
    try:
        _exit_code = main(sys.argv[1])
    except BaseException:
        # BaseException: a generated sys.exit()/SystemExit must produce a
        # traceback for the repair loop, not a silent exit code.
        traceback.print_exc()
        sys.exit(1)
    sys.exit(_exit_code)
