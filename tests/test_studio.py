# Copyright (C) 2026 Sugar Labs
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import subprocess
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_GTK_SANITIZED_VARS = (
    'LD_LIBRARY_PATH', 'GTK_PATH', 'GIO_MODULE_DIR',
    'GDK_PIXBUF_MODULE_FILE', 'GTK_EXE_PREFIX', 'GTK_IM_MODULE_FILE',
)


def _clean_gtk_env():
    return {
        key: value for key, value in os.environ.items()
        if key not in _GTK_SANITIZED_VARS
    }


def _gtk_display_available():
    if not (os.environ.get('DISPLAY') or
            os.environ.get('WAYLAND_DISPLAY')):
        return False
    probe = (
        'import gi\n'
        'gi.require_version("Gtk", "3.0")\n'
        'from gi.repository import Gtk\n'
        'result = Gtk.init_check()\n'
        'available = result[0] if isinstance(result, tuple) else result\n'
        'raise SystemExit(0 if available else 1)\n'
    )
    try:
        completed = subprocess.run(
            [sys.executable, '-c', probe],
            cwd=REPO_ROOT,
            env=_clean_gtk_env(),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


class TestStudioDecoupling(unittest.TestCase):

    def test_panel_imports_without_any_jarabe_module(self):
        code = (
            'import sys\n'
            'import ui.panel\n'
            'import ui.window\n'
            'import main\n'
            'bad = [m for m in sys.modules if m.startswith("jarabe")]\n'
            'assert not bad, "jarabe leaked into standalone studio: %s" '
            '% bad\n'
        )
        completed = subprocess.run(
            [sys.executable, '-c', code],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            0, completed.returncode,
            'decoupling check failed:\n%s%s'
            % (completed.stdout, completed.stderr))

    def test_clean_generation_error_text_strips_pipeline_prefixes(self):
        from ui.panel import _clean_generation_error_text

        self.assertEqual(
            'Drawing requests must use a Gtk.DrawingArea draw surface.',
            _clean_generation_error_text(
                'Provider could not generate valid activity code: '
                'Provider generated code did not pass validation: '
                'Drawing requests must use a Gtk.DrawingArea draw '
                'surface.'))
        self.assertEqual(
            'attempt_limit_reached: validation still failed',
            _clean_generation_error_text(
                'Provider could not repair activity code: '
                'attempt_limit_reached: validation still failed'))
        self.assertEqual('plain message',
                         _clean_generation_error_text('plain message'))
        self.assertEqual('', _clean_generation_error_text(None))

    def test_refinement_prompt_respects_backend_limit(self):
        from core.spec import MAX_PROMPT_LENGTH
        from ui.panel import CreateAIActivityPanel

        limited = CreateAIActivityPanel._limit_refinement_prompt(
            None, 'head' + ('x' * 20000) + 'tail')

        self.assertLessEqual(len(limited), MAX_PROMPT_LENGTH)
        self.assertTrue(limited.startswith('head'))
        self.assertTrue(limited.endswith('tail'))

    def test_auto_reference_provider_falls_back_to_configured_vision(self):
        from types import SimpleNamespace

        from ui.panel import CreateAIActivityPanel

        text_provider = SimpleNamespace(
            supports_image_input=lambda: False)
        vision_provider = SimpleNamespace(
            supports_image_input=lambda: True)
        providers = {
            'freemodel': text_provider,
            'gemini': vision_provider,
        }
        service = SimpleNamespace(
            resolve_provider=lambda name: providers.get(name))
        panel = SimpleNamespace(
            _selected_options={'provider': 'default'})

        name, provider = \
            CreateAIActivityPanel._resolve_reference_image_provider(
                panel, service, 'freemodel')

        self.assertEqual('gemini', name)
        self.assertIs(vision_provider, provider)

    def test_local_policy_never_falls_back_to_cloud_for_reference(self):
        from types import SimpleNamespace

        from ui.panel import CreateAIActivityPanel

        vision_provider = SimpleNamespace(
            supports_image_input=lambda: True)
        providers = {'gemini': vision_provider}
        service = SimpleNamespace(
            resolve_provider=lambda name: providers.get(name))
        panel = SimpleNamespace(_selected_options={
            'provider': 'default',
            'policy': 'local',
            'planner': 'rag',
        })

        unused_name, provider = \
            CreateAIActivityPanel._resolve_reference_image_provider(
                panel, service, 'local-template')

        self.assertIsNone(provider)


_OFFSCREEN_SCRIPT = '''
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

from ui.panel import CreateAIActivityPanel

window = Gtk.OffscreenWindow()
panel = CreateAIActivityPanel()
window.add(panel)
window.show_all()
panel.reset_view()
while Gtk.events_pending():
    Gtk.main_iteration_do(False)

assert panel._stack.get_visible_child_name() == 'home', \\
    panel._stack.get_visible_child_name()
assert panel._sidebar_visible is False
assert panel._sidebar_toggle_button.get_label() == '▶ Sidebar'
assert panel._live_edit_enabled is False
assert panel._live_edit_off_button.get_style_context().has_class(
    'create-ai-live-toggle-active')
assert not panel._live_edit_on_button.get_style_context().has_class(
    'create-ai-live-toggle-active')

panel.append_prompt_text('a fractions quiz for kids')
panel.cancel_generation()
while Gtk.events_pending():
    Gtk.main_iteration_do(False)

# Destroy the panel before the OffscreenWindow. GTK's OffscreenWindow
# segfaults if it disposes this widget tree itself during its own
# teardown; a real Gtk.Window (and the running app) tears the same panel
# down cleanly, so this is a harness-only teardown detail, not an app bug.
panel.destroy()
window.destroy()
print('OFFSCREEN-OK')
'''


_OFFSCREEN_HOME_SCRIPT = '''
import json
import os
import tempfile

sugar_home = tempfile.mkdtemp(prefix='aod-studio-home-test-')
os.environ['SUGAR_HOME'] = sugar_home

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

from ui.panel import CreateAIActivityPanel


def pump():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


window = Gtk.OffscreenWindow()
panel = CreateAIActivityPanel()
window.add(panel)
window.show_all()
panel.reset_view()
pump()

assert panel._stack.get_visible_child_name() == 'home'
assert panel._home_empty_box.get_visible()
assert len(panel._home_ring_icons) == 0

assert panel._enhance_button is not None
assert panel._selected_options['enhance'] == 'on'

# "Create new" now opens the prompt directly (the MODIFY/CREATE chooser
# was removed), so the stack goes straight to the create view.
panel._CreateAIActivityPanel__home_create_new_cb(None)
pump()
assert panel._stack.get_visible_child_name() == 'create'

panel._CreateAIActivityPanel__back_to_home_cb(None)
pump()
assert panel._stack.get_visible_child_name() == 'home'

project_dir = os.path.join(
    sugar_home, 'default', 'aod', 'projects', 'Demo.activity')
os.makedirs(os.path.join(project_dir, 'activity'))
with open(os.path.join(project_dir, 'aod_plan.json'), 'w',
          encoding='utf-8') as plan_file:
    json.dump({'name': 'Demo Activity', 'template': 'grid'}, plan_file)
with open(os.path.join(project_dir, 'activity', 'activity.svg'), 'w',
          encoding='utf-8') as icon_file:
    icon_file.write('<svg xmlns="http://www.w3.org/2000/svg"/>')

panel._refresh_home_projects()
pump()
assert len(panel._home_ring_icons) == 1
assert panel._home_ring.get_visible()
assert not panel._home_empty_box.get_visible()

# Destroy the panel first: see the note in _OFFSCREEN_SCRIPT above.
panel.destroy()
window.destroy()
print('OFFSCREEN-HOME-OK')
'''


_OFFSCREEN_TARGET_SCRIPT = '''
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

from ui.panel import CreateAIActivityPanel

window = Gtk.OffscreenWindow()
panel = CreateAIActivityPanel()
window.add(panel)
window.show_all()
panel.reset_view()
while Gtk.events_pending():
    Gtk.main_iteration_do(False)

# A click inside a canvas maps to a named 3x3 zone plus exact percentages.
assert panel._live_edit_zone(0, 0, 200, 100)[0] == 'top-left'
assert panel._live_edit_zone(100, 50, 200, 100)[0] == 'centre'
assert panel._live_edit_zone(200, 100, 200, 100)[0] == 'bottom-right'
zone, px, py = panel._live_edit_zone(150, 25, 200, 100)
assert zone == 'top-right', zone
assert (px, py) == (75, 25), (px, py)

# The description carries the zone and clamped percentages...
desc = panel._describe_canvas_point(
    'drawing canvas', 180, 20, (0, 0), (200, 100))
assert 'drawing canvas' in desc and 'top-right' in desc, desc
assert '90%' in desc and '20%' in desc, desc
# ...and the widget origin is subtracted before measuring.
desc2 = panel._describe_canvas_point(
    'drawing canvas', 60, 60, (40, 40), (40, 40))
assert '50%, 50%' in desc2, desc2

# Target kind drives the note the refinement backend receives.
panel._set_live_edit_target('drawing canvas - centre (50%, 50%)', kind='point')
assert panel._live_edit_target_kind == 'point'
assert not panel._live_edit_target_is_region
assert 'precise spot' in panel._preview_target_note()

panel._set_live_edit_target('area 10%, 10%', is_region=True)
assert panel._live_edit_target_kind == 'region'
assert 'dragged a selection' in panel._preview_target_note()

panel._set_live_edit_target('button: Clear')
assert panel._live_edit_target_kind == 'widget'
assert 'clicked this specific part' in panel._preview_target_note()

# Picking a target now returns (desc, widget, origin, size); nothing under
# the pointer yields a clean 4-tuple of Nones rather than crashing.
result = panel._pick_live_edit_target_at(window, -100, -100)
assert isinstance(result, tuple) and len(result) == 4, result
assert result[1] is None

panel.destroy()
window.destroy()
print('OFFSCREEN-TARGET-OK')
'''


_OFFSCREEN_ASK_BAR_SCRIPT = '''
import time
from types import SimpleNamespace

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gdk, GdkPixbuf, Gtk

from core.spec import ActivitySpec
import service.service as service_module
from ui.panel import CreateAIActivityPanel
from ui.reference_image import ReferenceImage

window = Gtk.OffscreenWindow()
panel = CreateAIActivityPanel()
window.add(panel)
window.show_all()
panel.reset_view()
while Gtk.events_pending():
    Gtk.main_iteration_do(False)

assert panel._ask_bar_entry is not None, 'ask bar entry missing'
assert panel._ask_bar_reference_button is not None, \
    'reference image button missing'
assert panel._prompt_reference_button is not None, \
    'create prompt reference image button missing'
assert panel._reference_image is None, 'reference should start empty'
assert not panel._ask_bar_reference_clear.get_visible(), \
    'studio remove button visible without attachment'
assert not panel._prompt_reference_clear.get_visible(), \
    'create remove button visible without attachment'

# Ctrl+V attaches a clipboard image in the studio prompt. A text clipboard
# remains available to the entry's normal paste behavior.
paste_pixbuf = GdkPixbuf.Pixbuf.new(
    GdkPixbuf.Colorspace.RGB, False, 8, 20, 10)
paste_pixbuf.fill(0x3366ccff)
clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
clipboard.set_image(paste_pixbuf)
paste_event = SimpleNamespace(
    state=Gdk.ModifierType.CONTROL_MASK,
    keyval=Gdk.KEY_v,
)
handled = panel._CreateAIActivityPanel__ask_bar_key_press_event_cb(
    panel._ask_bar_entry, paste_event)
assert handled, 'clipboard image paste was not handled'
deadline = time.monotonic() + 3
while panel._reference_loading_running and time.monotonic() < deadline:
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)
    time.sleep(0.01)
assert panel._reference_image is not None, 'clipboard image was not attached'
assert panel._reference_image.source_name == 'pasted-reference.png'
panel._clear_reference_image()
clipboard.set_text('ordinary copied text', -1)
assert not panel._CreateAIActivityPanel__ask_bar_key_press_event_cb(
    panel._ask_bar_entry, paste_event), 'text paste should remain native'

# The ask bar must submit without depending on the removed live-edit
# entry (which is always None now).
calls = []
panel._submit_refinement_from_prompt = (
    lambda text, source='chat': calls.append((source, text)))
send = panel._CreateAIActivityPanel__ask_bar_send_cb

panel._live_edit_enabled = False
panel._ask_bar_entry.set_text('make the score bigger')
send(None)
assert panel._ask_bar_entry.get_text() == '', 'entry not cleared after send'

panel._live_edit_enabled = True
panel._ask_bar_entry.set_text('change the button colour')
send(None)

# Blank input must not submit.
panel._ask_bar_entry.set_text('   ')
send(None)

assert calls == [
    ('chat', 'make the score bigger'),
    ('preview', 'change the button colour'),
], calls

# With an existing activity, an attached image can be sent without text and
# is routed to the asynchronous reference path instead of the text path.
pixbuf = GdkPixbuf.Pixbuf.new(
    GdkPixbuf.Colorspace.RGB, False, 8, 2, 1)
pixbuf.fill(0x3366ccff)
saved, image_data = pixbuf.save_to_bufferv('png', [], [])
assert saved
reference = ReferenceImage(
    data=bytes(image_data), mime_type='image/png', width=2, height=1,
    source_name='mockup.png', sha256='abc123')

panel._generation_result = object()
panel._reference_image = reference
panel._update_reference_image_ui()
assert panel._ask_bar_reference_clear.get_visible()
assert panel._prompt_reference_clear.get_visible()
window.show_all()
while Gtk.events_pending():
    Gtk.main_iteration_do(False)
assert panel._ask_bar_reference_clear.get_visible()
assert panel._prompt_reference_clear.get_visible()
reference_calls = []
panel._begin_reference_image_refinement = (
    lambda text, source: reference_calls.append((source, text)))
panel._live_edit_enabled = False
panel._ask_bar_entry.set_text('')
send(None)
assert reference_calls == [('chat', '')], reference_calls

# The initial creation prompt uses the same attachment path.
panel._generation_result = None
panel._set_prompt_text('build a science observation game')
panel._CreateAIActivityPanel__send_button_clicked_cb(None)
assert reference_calls[-1] == (
    'create', 'build a science observation game'), reference_calls

# The vision result is added only to the backend prompt. The learner-facing
# prompt stays readable, and the attachment is marked for the eventual job.
panel._reference_analysis_running = True
guided_calls = []
panel._begin_guided_generation = (
    lambda prompt, display_prompt=None:
    guided_calls.append((prompt, display_prompt)))
panel._reference_image_analysis_finished_cb(
    panel._reference_analysis_serial,
    'abc123',
    'build a science observation game',
    'create',
    'Reference image brief:\\n- Layout: two large cards',
    '',
)
assert len(guided_calls) == 1, guided_calls
assert 'two large cards' in guided_calls[0][0], guided_calls
assert guided_calls[0][1] == 'build a science observation game', guided_calls
assert panel._reference_pending_sha == 'abc123'

# Submitting the backend-expanded spec must keep the original learner prompt
# visible. The same in-memory mockup appears in the user bubble without being
# added to persisted session data or duplicated above the activity preview.
submitted = []
fake_job = SimpleNamespace(job_id='fake-job', session_id='fake-session')
fake_service = SimpleNamespace(
    submit_activity=lambda spec, **kwargs:
    (submitted.append((spec, kwargs)) or fake_job),
    watch=lambda job_id, callback: None,
)
service_module._service = fake_service
panel._resolve_generation_provider_name = lambda service: 'local-template'
panel._generation_job_updated_from_worker = lambda job: None
backend_spec = ActivitySpec(
    'Science Sort',
    'BACKEND CONFIRMED REQUIREMENTS THAT MUST STAY HIDDEN',
    'science',
    'MIT',
    categories=('science', 'games'),
)
panel._submit_generation_spec(
    backend_spec,
    chat_prompt='Original learner prompt',
    display_prompt='Original learner prompt',
)
assert submitted and submitted[0][0].prompt.startswith('BACKEND CONFIRMED')
assert panel._get_prompt_text() == 'Original learner prompt'

def descendants(widget):
    found = [widget]
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            found.extend(descendants(child))
    return found

reference_images = [
    widget for widget in descendants(panel._chat_messages_box)
    if isinstance(widget, Gtk.Image)
    and (widget.get_tooltip_text() or '').startswith('Reference:')
]
assert reference_images, 'left chat user bubble has no reference mockup'

# A completed unrelated job must not clear the attachment. Only the exact
# job that consumed this image owns cleanup.
panel._reference_inflight_sha = 'abc123'
panel._reference_inflight_job_id = 'reference-job'
assert not panel._clear_reference_for_completed_job('other-job')
assert panel._reference_image is not None
assert panel._clear_reference_for_completed_job('reference-job')
assert panel._reference_image is None
window.show_all()
while Gtk.events_pending():
    Gtk.main_iteration_do(False)
assert not panel._ask_bar_reference_clear.get_visible()
assert not panel._prompt_reference_clear.get_visible()

panel.destroy()
window.destroy()
print('OFFSCREEN-ASKBAR-OK')
'''


_OFFSCREEN_GUIDED_SCRIPT = '''
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

from llm.clarify import format_answers
from ui.panel import CreateAIActivityPanel


def pump():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


window = Gtk.OffscreenWindow()
panel = CreateAIActivityPanel()
window.add(panel)
window.show_all()
panel.reset_view()
pump()

# The guided page lives in the studio preview column and its scaffolding
# is visible (regression: an all-hidden child produced a blank screen).
assert panel._studio_mode_stack.get_child_by_name('guided') is not None
assert panel._guided_view.get_visible()

questions = [
    {'id': 'mode', 'label': 'Who plays?', 'type': 'single',
     'options': ['Human vs AI', '2-player']},
    {'id': 'features', 'label': 'Which features?', 'type': 'multi',
     'options': ['Undo', 'Clock']},
    {'id': 'else', 'label': 'Anything else?', 'type': 'text'},
]
panel._guided_state = {
    'prompt': 'chess', 'spec': None, 'provider': None,
    'questions': questions, 'answers': {}, 'answers_text': '',
    'answer_widgets': {}, 'plan_text': '', 'discussion': [],
}
panel._show_questions_page(questions)
pump()
children = panel._guided_body.get_children()
assert children, 'questions page has no widgets'
assert any(w.get_visible() for w in children), 'questions widgets hidden'
assert set(panel._guided_state['answer_widgets']) == {
    'mode', 'features', 'else'}

panel._collect_guided_answers()
assert isinstance(panel._guided_state['answers'], dict)

panel._guided_state['answers'] = {'mode': 'Human vs AI'}

# Continue goes straight to building — there is no separate plan-review
# step. The answers are folded into the prompt for the normal submit path.
captured = {}
panel._submit_generation_from_prompt = (
    lambda prompt, chat_prompt=None, display_prompt=None: captured.update(
        prompt=prompt, chat_prompt=chat_prompt,
        display_prompt=display_prompt))
panel._commit_guided_and_build()
assert 'chess' in captured['prompt'], captured
assert 'Confirmed requirements' in captured['prompt'], captured
assert 'Human vs AI' in captured['prompt'], captured
assert captured['chat_prompt'] == 'chess'
assert captured['display_prompt'] == 'chess'
assert panel._guided_state is None

panel.destroy()
window.destroy()
print('OFFSCREEN-GUIDED-OK')
'''


_OFFSCREEN_GUIDED_TRIGGER_SCRIPT = '''
import threading
import time

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

import llm.clarify as clarify
from ui.panel import CreateAIActivityPanel


def pump():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


def all_label_text(widget):
    texts = []
    getter = getattr(widget, 'get_children', None)
    if getter is None:
        return texts
    for child in getter():
        if isinstance(child, Gtk.Label):
            texts.append(child.get_text())
        texts.extend(all_label_text(child))
    return texts


class _FakeProvider:
    name = 'openrouter'
    model = 'test'


QUESTIONS = [
    {'id': 'mode', 'label': 'Who plays?', 'type': 'single',
     'options': ['A', 'B']},
    {'id': 'extra', 'label': 'Anything else?', 'type': 'text'},
]
questions_release = threading.Event()


def paused_questions(provider, spec, timeout=90):
    questions_release.wait(2)
    return QUESTIONS


clarify.generate_questions = paused_questions

window = Gtk.OffscreenWindow()
window.set_default_size(1200, 900)
panel = CreateAIActivityPanel()
window.add(panel)
window.show_all()
panel.reset_view()
pump()

# Sending an idea must open the questionnaire in the studio (regression:
# it fell straight through to the blank preview when the guided flow or
# provider resolution failed).
panel._resolve_active_provider = lambda: _FakeProvider()
panel._begin_guided_generation('chess')
pump()

# Assert during the asynchronous thinking screen, before any question has
# rendered. The old regression test only checked afterward and missed the
# visible sidebar flash.
assert panel._guided_running
assert panel._sidebar_visible is False
assert not panel._studio_right_panel.get_visible()

questions_release.set()

ok = False
for _ in range(400):
    pump()
    if (panel._stack.get_visible_child_name() == 'studio' and
            panel._studio_mode_stack.get_visible_child_name() == 'guided'):
        labels = all_label_text(panel._guided_body)
        if any('Who plays' in text for text in labels):
            ok = True
            break
    time.sleep(0.01)

assert ok, 'guided questions did not render after Send'
assert panel._sidebar_visible is False
assert not panel._studio_right_panel.get_visible()
assert abs(panel._inner_paned.get_position() -
           panel._inner_paned.get_allocated_width()) <= 1, (
    panel._inner_paned.get_position(),
    panel._inner_paned.get_allocated_width())

# While the questionnaire is open the studio tabs are locked so the user
# cannot navigate away from the questions mid-answer.
assert not panel._studio_preview_tab.get_sensitive()
assert not panel._studio_review_tab.get_sensitive()
assert all(not pill.get_sensitive() for pill in panel._studio_action_pills)

# On the studio the content fills top-to-bottom (no floating margins).
assert panel._content_alignment.get_property('yscale') == 1.0, \\
    panel._content_alignment.get_property('yscale')

panel.destroy()
window.destroy()
print('OFFSCREEN-GUIDED-TRIGGER-OK')
'''


_OFFSCREEN_CHAT_AVATAR_SCRIPT = '''
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

from ui.panel import CreateAIActivityPanel


def pump():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


def all_label_text(widget):
    texts = []
    getter = getattr(widget, 'get_children', None)
    if getter is None:
        return texts
    for child in getter():
        if isinstance(child, Gtk.Label):
            texts.append(child.get_text())
        texts.extend(all_label_text(child))
    return texts


window = Gtk.OffscreenWindow()
panel = CreateAIActivityPanel()
window.add(panel)
window.show_all()
panel.reset_view()
pump()

# An AI message is presented as "Sparky" with the round drawn avatar.
panel._add_chat_bubble('Hello there!', from_user=False, scroll=False)
pump()
labels = all_label_text(panel._chat_messages_box)
assert 'Sparky' in labels, labels

# The thinking indicator also carries the Sparky avatar/name.
row = panel._show_typing_bubble(panel._chat_messages_box, None)
pump()
assert row is not None
typing_labels = all_label_text(row)
assert any('Sparky' in text for text in typing_labels), typing_labels
assert any('thinking' in text for text in typing_labels), typing_labels
assert getattr(row, '_typing_dots', None) is not None
panel._remove_typing_bubble(row)

panel.destroy()
window.destroy()
print('OFFSCREEN-CHAT-AVATAR-OK')
'''


_OFFSCREEN_STEPS_SCRIPT = '''
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

from ui.panel import CreateAIActivityPanel


def pump():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


def all_label_text(widget):
    texts = []
    getter = getattr(widget, 'get_children', None)
    if getter is None:
        return texts
    for child in getter():
        if isinstance(child, Gtk.Label):
            texts.append(child.get_text())
        texts.extend(all_label_text(child))
    return texts


window = Gtk.OffscreenWindow()
panel = CreateAIActivityPanel()
window.add(panel)
window.show_all()
panel.reset_view()
pump()

panel._start_generation_steps()
pump()
labels = all_label_text(panel._chat_messages_box)
assert 'Thinking through your idea' in labels, labels
assert 'Building your activity' in labels, labels
assert 'Getting it ready to play' in labels, labels

# Advancing to code generation marks earlier steps done and this one active.
panel._update_generation_steps('generating')
pump()
assert panel._step_rows['planning']._step_state == 'done'
assert panel._step_rows['grounding']._step_state == 'done'
assert panel._step_rows['writing']._step_state == 'active'
assert panel._step_rows['packaging']._step_state == 'pending'

# The active step is emphasised and a live sub-status reflects the work.
assert panel._step_labels['writing'].get_style_context().has_class(
    'create-ai-step-label-active')
panel._set_step_substatus('Writing with the model…')
pump()
assert panel._step_sub_label.get_text() == 'Writing with the model…'

# Finishing marks everything done and flips the name to Done.
panel._finish_generation_steps()
pump()
assert all(icon._step_state == 'done'
           for icon in panel._step_rows.values())
assert any('Done' in text for text in all_label_text(panel._step_widget))

panel.destroy()
window.destroy()
print('OFFSCREEN-STEPS-OK')
'''


_OFFSCREEN_LEARNING_SCRIPT = '''
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

from ui.panel import CreateAIActivityPanel
from core.spec import ActivitySpec
from generation.generator import enrich_plan
from generation.templates import render_activity_source


def pump():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


class FakeResult:
    def __init__(self, source, plan, spec):
        self.files = {'activity.py': source}
        self.plan = plan
        self.spec = spec
        self.provider = 'fake'
        self.model = 'fake-1'
        self.project_path = '/tmp/aod-fake'


window = Gtk.OffscreenWindow()
panel = CreateAIActivityPanel()
window.add(panel)
window.show_all()
panel.reset_view()
pump()

assert panel._sidebar_reflection_box is not None
assert panel._sidebar_annotation_box is not None
assert set(panel._sidebar_tab_buttons) == {
    'challenges', 'reflections', 'annotations'}

spec = ActivitySpec('Fraction Quest', 'Make a fractions quiz.',
                    'logic_math', 'MIT')
plan = enrich_plan(spec, {'template': 'quiz', 'summary': 's',
                          'learner_goal': 'g',
                          'learner_steps': ['a', 'b', 'c']})
source = render_activity_source(spec, plan)

panel._update_sidebar_learning(FakeResult(source, plan, spec), plan)
pump()
refl = panel._sidebar_reflection_box.get_children()
anno = panel._sidebar_annotation_box.get_children()
assert len(refl) >= 4, 'reflection cards: %d' % len(refl)
assert len(anno) >= 5, 'annotation cards: %d' % len(anno)

panel._show_sidebar_tab('reflections')
pump()
assert panel._sidebar_reflection_box.get_visible()
assert not panel._sidebar_challenge_box.get_visible()

new_source = source.replace('Fraction Quest', 'Fraction Adventure')
panel._update_sidebar_learning(
    FakeResult(new_source, plan, spec), plan, source)
pump()
assert len(panel._sidebar_reflection_box.get_children()) > len(refl)

# Destroy the panel first: see the note in _OFFSCREEN_SCRIPT above.
panel.destroy()
window.destroy()
print('OFFSCREEN-LEARNING-OK')
'''


_OFFSCREEN_VERSIONS_SCRIPT = '''
import os
import tempfile

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

from ui.panel import CreateAIActivityPanel
from service.sessions import AODRevision


def pump():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


def _project(source):
    path = tempfile.mkdtemp(prefix='aod-ver-')
    with open(os.path.join(path, 'activity.py'), 'w') as handle:
        handle.write(source)
    return path


V1 = ('class GeneratedActivity:\\n'
      '    def __init__(self):\\n'
      '        pass\\n')
V2 = ('class GeneratedActivity:\\n'
      '    def __init__(self):\\n'
      '        pass\\n'
      '    def added_helper(self):\\n'
      '        return 1\\n')

r1 = AODRevision.create('job1', 'make a quiz',
                        {'project_path': _project(V1), 'activity_name': 'Quiz',
                         'provider': 'p', 'model': 'm', 'template': 'quiz'})
r2 = AODRevision.create('job2', 'add a helper',
                        {'project_path': _project(V2), 'activity_name': 'Quiz',
                         'provider': 'p', 'model': 'm', 'template': 'quiz'},
                        parent_revision_id=r1.revision_id)

window = Gtk.OffscreenWindow()
panel = CreateAIActivityPanel()
window.add(panel)
window.show_all()
panel.reset_view()
pump()

# Two real revisions: history, compare dropdowns, and a real diff.
panel._aod_session_id = 'test-session'
panel._get_session_revisions = lambda: [r1, r2]
panel._refresh_version_history()
pump()

assert len(panel._version_history_box.get_children()) == 2, 'history cards'
assert panel._version_compare_after.get_model().iter_n_children(None) == 2, \\
    'compare dropdown not populated'
assert panel._diff_after == r2.revision_id, 'after default not newest'
assert panel._diff_before == r1.revision_id, 'before default not parent'
assert panel._get_version_diff_pair() == (r1.revision_id, r2.revision_id)

lines = panel._get_version_diff_lines()
assert any(marker == '+' and 'added_helper' in text
           for marker, text in lines), 'added method missing from diff'

# One revision: nothing to compare.
panel._get_session_revisions = lambda: [r1]
panel._refresh_version_history()
pump()
assert not panel._version_compare_before.get_sensitive(), 'compare not disabled'
assert panel._get_version_diff_pair() == ('', ''), 'unexpected diff pair'

# No session: honest empty state, no fabricated versions.
panel._aod_session_id = ''
panel._get_session_revisions = lambda: []
panel._refresh_version_history()
pump()
assert panel._get_version_history() == [], 'fabricated placeholder versions'

# Destroy the panel first: see the note in _OFFSCREEN_SCRIPT above.
panel.destroy()
window.destroy()
print('OFFSCREEN-VERSIONS-OK')
'''


_OFFSCREEN_PREVIEW_KEYS_SCRIPT = '''
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk

from ui.panel import CreateAIActivityPanel


def pump():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


class FakeActivity:
    def __init__(self):
        self.emitted = []

    def emit(self, signal, event):
        self.emitted.append(signal)


class FakeEvent:
    def __init__(self, etype):
        self.type = etype


window = Gtk.OffscreenWindow()
panel = CreateAIActivityPanel()
window.add(panel)
window.show_all()
panel.reset_view()
pump()

forward = panel._CreateAIActivityPanel__preview_key_event_cb

# Key presses/releases are forwarded to the previewed activity instance so
# handlers connected on the activity window (self) fire even though that
# window is never shown in the preview.
panel._live_preview_activity = FakeActivity()
assert forward(None, FakeEvent(Gdk.EventType.KEY_PRESS)) is False
assert forward(None, FakeEvent(Gdk.EventType.KEY_RELEASE)) is False
assert panel._live_preview_activity.emitted == [
    'key-press-event', 'key-release-event'], panel._live_preview_activity.emitted

# No previewed activity: forwarding is a safe no-op, never raises.
panel._live_preview_activity = None
assert forward(None, FakeEvent(Gdk.EventType.KEY_PRESS)) is False

# Focus helper is a safe no-op when there is no canvas.
panel._live_preview_canvas = None
assert panel._focus_live_preview_canvas() is False

panel.destroy()
window.destroy()
print('OFFSCREEN-PREVIEW-KEYS-OK')
'''


@unittest.skipUnless(
    _gtk_display_available(), 'needs a usable display server')
class TestStudioOffscreen(unittest.TestCase):

    def _run_offscreen(self, script):
        clean_env = _clean_gtk_env()
        try:
            return subprocess.run(
                [sys.executable, '-c', script],
                cwd=REPO_ROOT,
                env=clean_env,
                capture_output=True,
                text=True,
                timeout=90,
            )
        except subprocess.TimeoutExpired as expired:
            self.fail('offscreen script timed out:\n%s\n%s'
                      % (expired.stdout, expired.stderr))

    def test_home_gallery_empty_state_and_refresh(self):
        completed = self._run_offscreen(_OFFSCREEN_HOME_SCRIPT)
        self.assertEqual(
            0, completed.returncode,
            'offscreen home test failed:\n%s%s'
            % (completed.stdout, completed.stderr))
        self.assertIn('OFFSCREEN-HOME-OK', completed.stdout)

    def test_panel_starts_on_home_and_survives_lifecycle(self):
        # Sanitized-env subprocess: snap/IDE shells leak
        # LD_LIBRARY_PATH/GTK_PATH values that make GTK hang, and a
        # subprocess isolates GTK state from other tests.
        completed = self._run_offscreen(_OFFSCREEN_SCRIPT)
        self.assertEqual(
            0, completed.returncode,
            'offscreen smoke failed:\n%s%s'
            % (completed.stdout, completed.stderr))
        self.assertIn('OFFSCREEN-OK', completed.stdout)

    def test_ask_bar_submits_in_both_modes(self):
        completed = self._run_offscreen(_OFFSCREEN_ASK_BAR_SCRIPT)
        self.assertEqual(
            0, completed.returncode,
            'ask bar test failed:\n%s%s'
            % (completed.stdout, completed.stderr))
        self.assertIn('OFFSCREEN-ASKBAR-OK', completed.stdout)

    def test_live_edit_targets_a_precise_canvas_point(self):
        completed = self._run_offscreen(_OFFSCREEN_TARGET_SCRIPT)
        self.assertEqual(
            0, completed.returncode,
            'offscreen target test failed:\n%s%s'
            % (completed.stdout, completed.stderr))
        self.assertIn('OFFSCREEN-TARGET-OK', completed.stdout)

    def test_guided_flow_renders_and_builds(self):
        completed = self._run_offscreen(_OFFSCREEN_GUIDED_SCRIPT)
        self.assertEqual(
            0, completed.returncode,
            'offscreen guided test failed:\n%s%s'
            % (completed.stdout, completed.stderr))
        self.assertIn('OFFSCREEN-GUIDED-OK', completed.stdout)

    def test_learning_sidebar_populates_and_switches_tabs(self):
        completed = self._run_offscreen(_OFFSCREEN_LEARNING_SCRIPT)
        self.assertEqual(
            0, completed.returncode,
            'offscreen learning-sidebar test failed:\n%s%s'
            % (completed.stdout, completed.stderr))
        self.assertIn('OFFSCREEN-LEARNING-OK', completed.stdout)

    def test_versions_compare_uses_real_revisions(self):
        completed = self._run_offscreen(_OFFSCREEN_VERSIONS_SCRIPT)
        self.assertEqual(
            0, completed.returncode,
            'offscreen versions test failed:\n%s%s'
            % (completed.stdout, completed.stderr))
        self.assertIn('OFFSCREEN-VERSIONS-OK', completed.stdout)

    def test_preview_forwards_keys_to_activity(self):
        completed = self._run_offscreen(_OFFSCREEN_PREVIEW_KEYS_SCRIPT)
        self.assertEqual(
            0, completed.returncode,
            'offscreen preview-keys test failed:\n%s%s'
            % (completed.stdout, completed.stderr))
        self.assertIn('OFFSCREEN-PREVIEW-KEYS-OK', completed.stdout)

    def test_guided_flow_triggers_on_send_and_studio_fills(self):
        completed = self._run_offscreen(_OFFSCREEN_GUIDED_TRIGGER_SCRIPT)
        self.assertEqual(
            0, completed.returncode,
            'offscreen guided trigger test failed:\n%s%s'
            % (completed.stdout, completed.stderr))
        self.assertIn('OFFSCREEN-GUIDED-TRIGGER-OK', completed.stdout)

    def test_chat_ai_messages_carry_mr_john_avatar(self):
        completed = self._run_offscreen(_OFFSCREEN_CHAT_AVATAR_SCRIPT)
        self.assertEqual(
            0, completed.returncode,
            'offscreen chat avatar test failed:\n%s%s'
            % (completed.stdout, completed.stderr))
        self.assertIn('OFFSCREEN-CHAT-AVATAR-OK', completed.stdout)

    def test_generation_step_list_advances_and_completes(self):
        completed = self._run_offscreen(_OFFSCREEN_STEPS_SCRIPT)
        self.assertEqual(
            0, completed.returncode,
            'offscreen steps test failed:\n%s%s'
            % (completed.stdout, completed.stderr))
        self.assertIn('OFFSCREEN-STEPS-OK', completed.stdout)

    def test_prompt_activity_name_updates_spec_name(self):
        completed = self._run_offscreen(_OFFSCREEN_PROMPT_NAME_SCRIPT)
        self.assertEqual(
            0, completed.returncode,
            'offscreen prompt activity name test failed:\n%s%s'
            % (completed.stdout, completed.stderr))
        self.assertIn('OFFSCREEN-PROMPT-NAME-OK', completed.stdout)


_OFFSCREEN_PROMPT_NAME_SCRIPT = '''
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

from ui.panel import CreateAIActivityPanel
from core.spec import ActivitySpec

window = Gtk.OffscreenWindow()
panel = CreateAIActivityPanel()
window.add(panel)
window.show_all()

class MockResult:
    def __init__(self):
        self.spec = ActivitySpec('Old Name', 'prompt', 'category', 'MIT')
        self.bundle_path = '/tmp/fake.xo'

panel._generation_result = MockResult()

class MockNameDialog:
    def __init__(self, **kwargs):
        self._content = Gtk.VBox()
    def add_button(self, text, response_id):
        return Gtk.Button(label=text)
    def set_default_response(self, response_id):
        pass
    def set_decorated(self, decorated):
        pass
    def get_style_context(self):
        return Gtk.Button().get_style_context()
    def set_size_request(self, w, h):
        pass
    def get_content_area(self):
        return self._content
    def response(self, response_id):
        pass
    def run(self):
        def _find_entry(container):
            for child in container.get_children():
                if isinstance(child, Gtk.Entry):
                    return child
                if hasattr(child, 'get_children'):
                    found = _find_entry(child)
                    if found is not None:
                        return found
            return None
        entry = _find_entry(self._content)
        if entry is not None:
            entry.set_text('Super Fun Math')
        return Gtk.ResponseType.ACCEPT
    def destroy(self):
        pass

original_dialog = Gtk.Dialog
Gtk.Dialog = MockNameDialog

res = panel._prompt_activity_name()
assert res is True
assert panel._generation_result.spec.name == 'Super Fun Math'
assert panel._generation_result.bundle_path == ''

Gtk.Dialog = original_dialog
panel.destroy()
window.destroy()
print('OFFSCREEN-PROMPT-NAME-OK')
'''


if __name__ == '__main__':
    unittest.main()

