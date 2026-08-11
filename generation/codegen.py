# Copyright (C) 2026 Sugar Labs
#
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import re

from generation.prompts import extract_json_object
from generation.rag import get_api_reference
from generation.rag import get_ui_design_reference
from generation.validator import ALLOWED_IMPORT_ROOTS
from generation.validator import FORBIDDEN_CALLS
from generation.validator import FORBIDDEN_IMPORT_ROOTS
from generation.validator import _module_available


_CODE_SIZE_INSTRUCTIONS = {
    'compact': (
        'Length: Write a focused, complete activity in roughly 300–500 lines. '
        'Implement the core interaction fully but skip decorative extras. '
        'Every line must be functional — no placeholders or stubs.'
    ),
    'standard': (
        'Length: Write all necessary code to fully realize the request. '
        'For complex activities (board games, simulators) this typically '
        'requires 800–1200 lines. Prioritize completeness over brevity; '
        'do not abbreviate or stop early.'
    ),
    'full': (
        'Length: Write the most complete, polished version possible with no '
        'token limit. Include rich toolbar actions, keyboard shortcuts, '
        'multiple screens/modes, full Journal persistence, edge-case '
        'handling, and detailed visual polish. 1500+ lines is expected for '
        'anything non-trivial. Do not stop until the activity is '
        'production-ready.'
    ),
}


def _rendering_guidance():
    """Backend guidance for the system prompt, matched to this system.

    pygame/sugargame are only offered when the runtime actually has
    them; otherwise the model is steered to cairo so generated code can
    always preview and launch on this machine.
    """
    if _module_available('pygame') and _module_available('sugargame'):
        return (
            '## Rendering approach — cairo vs pygame/sugargame\n'
            'Choose the right rendering backend for the request:\n'
            '- **GTK3 + cairo (default)**: Use for most activities — '
            'board games, drawing apps, quizzes, writing tools, '
            'simulations. Gtk.DrawingArea with a cairo draw callback is '
            'idiomatic Sugar and integrates cleanly with GTK events. '
            'Prefer this unless the request is clearly game-loop '
            'driven.\n'
            '- **pygame via sugargame**: Use ONLY when the request '
            'explicitly asks for a pygame game or when the activity '
            'needs a continuous game loop (e.g. real-time arcade, '
            'physics simulation, animation-heavy game). When using '
            'pygame, wrap the activity with sugargame.canvas.Canvas and '
            'run pygame.display.set_mode inside the Sugar handle. '
            'Import sugargame.canvas and pygame. Still inherit from '
            'sugar3.activity.activity.Activity. Still use the Sugar '
            'toolbar and Journal persistence.\n'
            'When in doubt, use cairo + GTK3. Only choose sugargame '
            'when the learner prompt clearly describes gameplay that '
            'needs a frame loop.\n\n'
        )
    return (
        '## Rendering approach — GTK3 + cairo only\n'
        'The pygame/sugargame libraries are NOT installed on this '
        'system, so never import them. Build every activity — '
        'including arcade-style and real-time games — with GTK3 + '
        'cairo: draw in a Gtk.DrawingArea draw callback, drive the '
        'frame loop with GLib.timeout_add (about 33 ms per frame), '
        'and handle controls with GTK key-press-event handlers.\n'
        '## Keyboard input — make keys actually work\n'
        'A Gtk.DrawingArea receives key events ONLY when it is focusable '
        'and currently holds keyboard focus. For any activity driven by '
        'the keyboard (arrow keys, WASD, space) do ALL of the following, '
        'or the keys will silently do nothing:\n'
        '- On the drawing area: set_can_focus(True) and '
        'add_events(Gdk.EventMask.KEY_PRESS_MASK | '
        'Gdk.EventMask.KEY_RELEASE_MASK).\n'
        "- Connect the drawing area's 'key-press-event' (and "
        "'key-release-event' when you track held keys) to a handler.\n"
        '- Give it focus AFTER it is on screen: call grab_focus() from a '
        "'realize' or 'map' handler on the drawing area (and again on "
        "'button-press-event' so a click refocuses it). grab_focus() in "
        '__init__ runs too early and has no effect.\n'
        "- ALSO connect 'key-press-event' on the Activity itself (self) "
        'as a fallback so keys keep working if the canvas loses focus.\n'
        '- In the handler compare event.keyval to Gdk.KEY_Left / '
        'Gdk.KEY_Right / Gdk.KEY_Up / Gdk.KEY_Down (and Gdk.KEY_w / '
        'Gdk.KEY_a / Gdk.KEY_s / Gdk.KEY_d / Gdk.KEY_space), update '
        'state, call queue_draw() to redraw, and return True when you '
        'consume the key.\n'
        '## Cairo drawing — pycairo API, not the C API\n'
        'When code refers to cairo.LinearGradient, cairo.RadialGradient, '
        'cairo.ImageSurface, cairo.Context, or any other bare cairo.* name, '
        'it MUST include `import cairo`; the draw callback otherwise crashes '
        'and the canvas appears blank.\n'
        'A cairo.Context starts a path with cr.new_path(); '
        'cr.begin_new_path() does not exist.\n'
        'Never attach custom attributes or helper methods to the context '
        '(for example `cr.ellipse = ...`): pycairo Context objects reject '
        'attribute assignment. To draw an ellipse, save(), translate(), '
        'scale(), arc(0, 0, 1, ...), restore(), then fill() or stroke().\n'
        'The draw handler receives a cairo.Context. Use its methods: '
        'set_source_rgb/rgba, rectangle, arc, move_to, line_to, fill, '
        'stroke, show_text. For gradients CONSTRUCT a pattern — '
        'cairo.LinearGradient(x0, y0, x1, y1) or cairo.RadialGradient('
        'cx0, cy0, r0, cx1, cy1, r1) — then add_color_stop_rgb/rgba(...) '
        'and cr.set_source(gradient). The context has NO '
        'pattern_create_linear / pattern_create_radial methods (those are '
        'the C cairo API); calling them raises AttributeError every frame '
        'and the canvas renders blank.\n\n'
    )


def build_codegen_system_prompt(
        spec, plan, references=(), code_size='standard'):
    """Build the provider prompt for a complete Sugar activity.py file."""
    return (
        'You are Sugar Activity on Demand, a code generator for Sugar '
        'activities.\n\n'
        'Return the complete activity.py source inside a single Python '
        'code fence and nothing else:\n'
        '```python\n<complete Python source for activity.py>\n```\n\n'
        'Do not wrap the source in JSON. Do not add explanation, notes, '
        'or any text before or after the fence. Every output token is '
        'precious, so spend them on the Python code, not on JSON escaping '
        'or commentary.\n\n'
        'The generated source must be a complete Sugar GTK3 activity '
        'that follows the same patterns as real installed Sugar '
        'activities.\n\n'
        'SUGAR ACTIVITY STRUCTURE (follow exactly):\n'
        '1. Start with a copyright header and SPDX line:\n'
        '   # Copyright (C) 2026 Sugar Labs\n'
        '   # SPDX-License-Identifier: GPL-3.0-or-later\n'
        '2. Import gi and require versions BEFORE importing GTK:\n'
        '   import gi\n'
        '   gi.require_version("Gtk", "3.0")\n'
        '   gi.require_version("Gdk", "3.0")\n'
        '3. Use gettext for any user-visible strings:\n'
        '   from gettext import gettext as _\n'
        '4. Set up logging:\n'
        '   import logging\n'
        '   _logger = logging.getLogger("GeneratedActivity")\n'
        '5. Import sugar3 modules FIRST before any Gtk imports:\n'
        '   from sugar3.activity import activity\n'
        '   from sugar3.graphics.toolbarbox import ToolbarBox\n'
        '   from sugar3.activity.widgets import ActivityToolbarButton\n'
        '   from sugar3.activity.widgets import StopButton\n'
        '   Always prefer sugar3 / sugar-toolkit-gtk3 APIs over raw Gtk '
        'equivalents. Use sugar3.graphics.style for colors, fonts, and icon '
        'sizes. Use sugar3.graphics.toolbutton.ToolButton for toolbar items. '
        'For grouped toolbar choices import '
        'sugar3.graphics.radiotoolbutton.RadioToolButton instead of raw '
        'Gtk.RadioToolButton. Sugar ToolButton/RadioToolButton wrappers use '
        'set_tooltip(...); raw Gtk widgets use set_tooltip_text(...), never '
        'set_tooltip(...). The Sugar grid constant is exactly '
        'style.GRID_CELL_SIZE (uppercase); style.grid_size does not exist. '
        'Use sugar3.graphics.alert for in-activity notifications. Fall back '
        'to plain GTK3 only when no Sugar wrapper exists.\n'
        '6. Class must be named GeneratedActivity(activity.Activity)\n'
        '7. In __init__:\n'
        '   - Call activity.Activity.__init__(self, handle)\n'
        '   - Create ToolbarBox with ActivityToolbarButton and StopButton. '
        'Insert every item using '
        'toolbar_box.toolbar.insert(item, position). ToolbarBox does NOT have '
        'an add_toolbar_button() method.\n'
        '   - Call self.set_toolbar_box(toolbar_box)\n'
        '   - Build the canvas with Gtk widgets\n'
        '   - Call self.set_canvas(canvas)\n'
        '   - Call self.show_all()\n'
        '   - For Gtk.Adjustment ranges, call set_lower() and set_upper(); '
        'Gtk.Adjustment does NOT have set_bounds().\n'
        '8. Implement read_file(self, file_path) and '
        'write_file(self, file_path)\n'
        '   for Journal persistence using json. Sugar supplies file_path; '
        'do not import os or call os.path.exists(). If an existence guard is '
        'needed, import GLib and use '
        'GLib.file_test(file_path, GLib.FileTest.EXISTS).\n\n'
        '%(rendering_guidance)s'
        'Hard requirements:\n'
        '- Build the specific activity described by activity_kind, '
        'interaction_model, ui_regions, toolbar_actions, palette_actions, '
        'feedback_model, color_model, responsive_behavior, learner_steps, '
        'and the learner prompt. Do not copy a canned local template.\n'
        '- The learner prompt is the product definition. Learning-area '
        'selections are discovery/RAG hints only. Never add math problems, '
        'number collecting, quizzes, vocabulary, assessment, reflection, '
        'explanation prompts, partner roles, teacher flow, or other learning '
        'mechanics unless the learner prompt, confirmed requirements, or '
        'reference image explicitly requests them. A race request becomes a '
        'proper race; a drawing request becomes a proper drawing activity; a '
        'tool request becomes the requested tool.\n'
        '- The RAG references below show how real Sugar activities are '
        'assembled. Follow the Sugar lifecycle and GTK patterns from those '
        'references, but create new code for this request.\n'
        '- Treat the plan.template value only as a reference family for '
        'metadata; the generated UI and behavior must follow the learner '
        'request.\n'
        '- When the learner prompt contains a Reference image brief, ignore '
        'all Ignore regions and treat Visible decisions as already settled. '
        'If a reference image is attached to the user message, inspect it '
        'directly before writing activity.py; use the written brief as '
        'additional context and as a fallback, never as a substitute for the '
        'pixels. Internally inventory every target region and visible control '
        'before coding, then implement that inventory one-for-one. Preserve '
        'approximate relative widths/heights, hierarchy, alignment, ordering, '
        'spacing, color relationships, labels, and selected/disabled states. '
        'Functional tool strips and challenge/status panels inside the target '
        'are activity content and must remain in their reference positions. '
        'Do not omit, merge, relocate, or redesign them because the generic '
        'Sugar composition guidance prefers fewer panels or toolbar actions. '
        'Strict priority is: explicit learner request; visible reference '
        'decisions in the target; Sugar-native treatment of host chrome and '
        'unspecified details; generic design preferences. Translate only '
        'browser/editor/Studio host chrome into native Sugar controls, and '
        'keep the preserved target regions responsive.\n'
        '- The visible activity must include the controls and work area '
        'needed for the learner prompt. If the prompt asks for two learners, '
        'include separate learner/team state and a turn or collaboration '
        'workflow. If it asks for drawing, implement pointer events and '
        'actual drawing state, not a static sample image.\n'
        '- This activity.py is the generated product. Do not return a '
        'preview card, explanation-only mockup, tiny demo, TODO, or '
        'placeholder. A teacher should be able to install it and have '
        'learners use the requested activity immediately.\n'
        '- Make the canvas/work area fill the activity window naturally with '
        'Gtk containers that expand. Avoid small centered toy panels unless '
        'the requested activity is intentionally compact.\n'
        '- Boards, grids, and play areas must scale with the window: '
        'compute cell/tile sizes from the allocated space (a size-allocate '
        'callback or an expanding Gtk.DrawingArea that redraws from its '
        'allocation), never hardcoded small pixel sizes. On a large screen '
        'the play area should use most of the window, centered, with '
        'square cells staying square.\n'
        '- This prompt is only for the first complete activity.py.  Later '
        'validation failures and refinements are handled with focused '
        'SEARCH/REPLACE repairs; never replace an existing file here.\n'
        '- Use only classroom-safe local state. No networking, subprocesses, '
        'or arbitrary filesystem access.\n'
        '- Keep the UI useful on 1024x768 screens.\n'
        '- Make the activity interactive enough for learners to try directly '
        'after installing it from the preview.\n'
        '- For a real-time game, keep keyboard/button state on `self`, never '
        'as a mutable class variable. Show the player, controls, goal, and '
        'start state before the simulation advances. Keep the player visibly '
        'identifiable during damage/invulnerability, and make restart reset '
        'all transient input, entity, timer, and game-over state. Every entity '
        'type and every delayed/random draw branch must use valid pycairo and '
        'survive after it appears—not only on the first frame.\n'
        '- Include the prompt-specific domain objects and actions. Examples: '
        'drawing prompts need DrawingArea pointer events and saved strokes; '
        'two-student prompts need visible learner roles and collaboration or '
        'turn-taking state; board games need actual board state, scoring or '
        'move rules; quiz prompts need input, feedback, and saved progress.\n'
        '\n## Quality bar — what "full-fledged" means\n'
        'When a learner describes an idea, ship a finished Sugar activity, '
        'not a stub or a generic application shell. '
        'Prompt fidelity, working behavior, safety, runtime correctness, and '
        'Journal persistence are hard requirements. The Sugar visual '
        'composition points are best-effort preferences: use them to improve '
        'the result, but never sacrifice requested features, reference '
        'fidelity, or a working activity merely to satisfy visual convention.\n'
        '- Reference-fidelity override: when a Reference image brief is '
        'present, the generic composition examples below apply only where '
        'the target image is silent. They must never be used to remove, move, '
        'merge, resize beyond responsive necessity, or restyle a visibly '
        'settled target activity region.\n'
        '- Prefer a finished Sugar-style UI: a dominant task-specific workspace, '
        'concise labels, symbolic controls with palettes/tooltips, and '
        'visible feedback for every action. It must read as an activity, '
        'not a three-button debug panel or a web landing page.\n'
        '- Multiple interaction modes / screens when the request implies '
        'them. Use Gtk.Stack with named pages for flows like setup → play '
        '→ result, or question → feedback → review. Put contextual options '
        'in Sugar palettes or ToolbarButton sub-toolbars; add a notebook, '
        'tray, or panel only when those controls must stay visible.\n'
        '- Real domain logic. Chess enforces legal moves and detects '
        'check. A quiz tracks score and retains answers. A drawing app '
        'stores stroke geometry and supports brush size + color + undo. '
        '"Looks like the thing" is the floor, not the ceiling.\n'
        '- Prefer native Sugar composition using style.GRID_CELL_SIZE, '
        'style.DEFAULT_SPACING, style.DEFAULT_PADDING, style.zoom, '
        'style.COLOR_*, and Sugar icon-size constants. Prefer ToolButton, '
        'ToggleToolButton, RadioToolButton, ColorToolButton, Palette, Icon, '
        'HTray/VTray, and Sugar alerts over hand-styled GTK substitutes. '
        'Let the Sugar GTK theme render ordinary widgets. Do not create a '
        'universal card system, blue brand headings, shadows, gradients, or '
        'rounded web-dashboard panels. Custom CSS is permitted only for '
        'request-specific visuals and must derive colors from style.COLOR_*.\n'
        '- Sugar-native icon language: use real Sugar Artwork icon_name '
        'values for every toolbar or repeated action. Never use emoji, '
        'Unicode symbols, ASCII arrows, or text-only Gtk.ToolButton controls '
        'as icons. Give every ToolButton a tooltip or palette. For setup and '
        'empty states, use one compact sugar3.graphics.icon.Icon beside one '
        'short instruction—not a decorated card.\n'
        '- Sugar-native color hierarchy: keep ToolbarBox and persistent '
        'chrome grayscale; keep the workspace predominantly white; use '
        'strong outlined pictograms and bright XO-like stroke/fill pairs '
        'inside the learner-created content. Do not build a row of pastel '
        'web cards or repeat the activity title as a marketing heading.\n'
        '- Polished native hierarchy: align controls to Sugar spacing '
        'constants, use sentence-case labels, consistent compact targets, '
        'and generous breathing room around learner work. Do not wrap every '
        'instruction, requirement, setting, and status value in separate '
        'Gtk.Frame boxes. Prefer one quiet status/instruction row and at most '
        'one compact contextual tray, grouped with whitespace or separators.\n'
        '- Primary actions such as Check, Play, Hint, Undo, Redo, Clear, '
        'Reset, and Next belong in ToolbarBox as Sugar ToolButtons with real '
        'Sugar Artwork icons and tooltips. Never place a tall, stretched, '
        'all-caps text action beside the work surface. Put related mode '
        'options in a ToolbarButton palette/sub-toolbar and reveal advanced '
        'settings only for the active mode.\n'
        '- Drawing/construction composition: give the expanding canvas all '
        'remaining space; summarize the challenge and criteria in one concise '
        'row; keep tool, size, color, symmetry, and similar choices in a '
        'compact tray or contextual palette. The learner must be able to '
        'start without scanning several competing panels.\n'
        '- Region discipline: the creation/play surface dominates. The '
        'canvas contains learner work, not rows of unrelated controls. Put '
        'primary actions in ToolbarBox, contextual actions in palettes or '
        'sub-toolbars, and keep only continuously relevant status/controls '
        'in a compact panel or tray. Never add a side dashboard merely to '
        'display every fact, score, instruction, and action at once.\n'
        '- Layout contract: the main work area DOMINATES the window — '
        'pack it expanding (True, True) so it fills all remaining space. '
        'Any truly necessary persistent panel stays compact (never more '
        'than one third of the window); every wrapped label in it must call '
        'set_max_width_chars(...) so it cannot starve the work area. A '
        'board/canvas draw callback must size from its '
        'allocation AND draw centered: compute the board edge from '
        'min(alloc.width, alloc.height) and offset by half the leftover '
        'in each axis, so the board fills its area instead of sitting '
        'small in a corner of dead space.\n'
        '- Proper Journal persistence via JSON: write_file serializes '
        'every piece of meaningful state (positions, scores, drawings, '
        'history). read_file restores it and rebuilds the visible UI.\n'
        '- Behavior quality: every visible control is connected to real '
        'model state and produces visible feedback. Tool/mode selection must '
        'change interaction; Undo/Redo/Reset enabled states stay synchronized; '
        'success is computed from learner state, never hardcoded; resizing '
        'keeps hit testing and drawing geometry aligned; Journal restore '
        'redraws the same visible state.\n'
        '- Purposeful toolbar: ActivityToolbarButton, Gtk.SeparatorToolItem, '
        'StopButton, plus the few primary actions used repeatedly while '
        'working. Use sugar3.graphics.toolbutton.ToolButton rather than raw '
        'Gtk.ToolButton. Move secondary modes/settings into Sugar palettes '
        'or ToolbarButton sub-toolbars instead of overcrowding the bar. '
        'Relevant actions such as New/Reset, Undo, Save Snapshot, '
        'Hint, or Change Tool/Color need icon_name and tooltip text.\n'
        '- Keyboard shortcuts for common actions (Ctrl+N new, Ctrl+Z '
        'undo, etc.) via Gtk.AccelGroup or key-press-event when relevant.\n'
        '- No TODO, no "placeholder", no "Add your code here", no demo '
        'strings. Every label and action is final classroom-ready text.\n'
        '- %(length_instruction)s\n'
        '\n## Examples of richness expected per request type\n'
        '- Drawing: tool palette (pen/eraser), color picker (≥6 colors), '
        'brush-size slider, undo/redo stack, clear-canvas action, save-'
        'as-PNG-to-Journal, stroke geometry persistence. Not just a '
        'DrawingArea with one black pen.\n'
        '- Quiz: question pool of 5+ items, randomized order, typed or '
        'multiple-choice answers, per-question feedback, running score, '
        'final review screen, restart action, Journal-saved progress.\n'
        '- Board game (chess/carrom/etc.): full board widget with '
        'visible coordinates, piece/coin rendering, turn indicator, '
        'legal-move enforcement, captured-pieces tray, and an optional '
        'collapsible move-log tray when the request benefits from it, plus '
        'reset and save actions.\n'
        '- Writing/narrative: titled text area with starter prompt, '
        'word/character counter live-updating, save-draft + load-draft '
        'actions, concise status, and optional formatting actions in a '
        'contextual toolbar or palette—not a permanent reflection sidebar.\n'
        '- Two-learner / partner: explicit Student A / Student B '
        'labels, visible "active turn" indicator, switch-turn button, '
        'per-learner score or contribution tally, swap-roles action.\n\n'
        'Allowed import roots: %(allowed)s\n'
        'Forbidden import roots: %(forbidden_imports)s\n'
        'Forbidden calls: %(forbidden_calls)s\n\n'
        'Sugar Activity API reference:\n%(api_reference)s\n\n'
        'Sugar whole-interface design reference:\n%(ui_reference)s\n\n'
        'Retrieved Sugar references:\n%(references)s'
    ) % {
        'rendering_guidance': _rendering_guidance(),
        'allowed': ', '.join(sorted(ALLOWED_IMPORT_ROOTS)),
        'forbidden_imports': ', '.join(sorted(FORBIDDEN_IMPORT_ROOTS)),
        'forbidden_calls': ', '.join(sorted(FORBIDDEN_CALLS)),
        'api_reference': get_api_reference(),
        'ui_reference': get_ui_design_reference(),
        'references': _format_references(references) or
        'No extra references were retrieved.',
        'length_instruction': _CODE_SIZE_INSTRUCTIONS.get(
            code_size, _CODE_SIZE_INSTRUCTIONS['standard']),
    }


def build_codegen_user_prompt(spec, plan, validation_feedback=''):
    """Describe the requested activity source to the provider."""
    feedback_block = ''
    if validation_feedback:
        feedback_block = (
            '\n\nAdditional requirements for this one initial source:\n%s'
            % validation_feedback
        )
    return (
        'Create activity.py for this Sugar activity request.\n\n'
        'Generate the real learner activity now. The output must be complete '
        'runnable GTK3/Sugar code, not a sketch, template note, preview '
        'description, static sample image, or generic local template. '
        'Before returning, self-check that the code implements the concrete '
        'nouns and verbs in the learner prompt.\n\n'
        'Structured request:\n%s\n\n'
        'Normalized plan JSON:\n%s%s'
    ) % (
        spec.to_prompt(),
        json.dumps(plan, indent=2, sort_keys=True),
        feedback_block,
    )


def extract_activity_source(value):
    """Extract Python source from provider JSON or a fenced code string."""
    source = ''
    if isinstance(value, dict):
        files = value.get('files')
        if isinstance(files, dict):
            source = _string_or_nested(
                files.get('activity.py')) or _string_or_nested(
                files.get('activity_py')) or ''
            if not source:
                for path, content in files.items():
                    if (isinstance(path, str)
                            and path.endswith('activity.py')
                            and isinstance(content, str)):
                        source = content
                        break
        elif isinstance(files, list):
            for item in files:
                if not isinstance(item, dict):
                    continue
                path = item.get('path') or item.get('name')
                if (isinstance(path, str)
                        and (path in ('activity.py', './activity.py')
                             or path.endswith('/activity.py'))):
                    source = _string_or_nested(
                        item.get('content')) or _string_or_nested(
                        item.get('source')) or ''
                    break
        if not source:
            for key in ('activity_py', 'activity.py', 'source', 'code'):
                candidate = value.get(key)
                if isinstance(candidate, str):
                    source = candidate
                    break
    elif isinstance(value, str):
        source = value
    else:
        raise ValueError('Provider code response must be text or JSON.')

    source = _strip_code_fence(source)
    if not source:
        raise ValueError('Provider code response did not include activity.py.')

    # A truncated JSON wrapper (e.g. {"activity_py": "...<cut off>) looks
    # like Python to a naive reader but is not valid Python.  Detect it
    # here so the caller gets a clear "truncated" message instead of a
    # confusing syntax error from ast.parse on the JSON text.
    stripped = source.lstrip()
    if stripped.startswith('{') or stripped.startswith('['):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            raise ValueError(
                'Model response was truncated: the JSON wrapping the '
                'activity source is incomplete. This usually means the '
                'model ran out of output tokens (finish_reason=length). '
                'Try a smaller prompt or a model with a larger output '
                'budget.'
            )
        if isinstance(parsed, dict):
            return extract_activity_source(parsed)

    if 'GeneratedActivity' not in source:
        raise ValueError(
            'Provider code response did not define GeneratedActivity.'
        )
    return source.rstrip() + '\n'


def _string_or_nested(value):
    """Return ``value`` as source text, descending one nested level.

    Some models wrap the file body one level deeper (e.g.
    ``{"activity.py": {"content": "..."}}``); a non-string value used to
    reach ``.strip()`` and crash with AttributeError instead of the clean
    'did not include activity.py' error.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ('content', 'source', 'code', 'text'):
            nested = value.get(key)
            if isinstance(nested, str):
                return nested
    return ''


def extract_activity_source_from_response(text):
    """Extract activity.py from a raw codegen text response.

    Models may return the source inside a ```python fence or as a JSON
    object with an activity_py field.  Truncated JSON responses are
    detected and reported clearly instead of being misread as Python.
    Model error messages (e.g. "ERROR: Cannot read image.png") are
    detected so the caller sees the real provider error instead of a
    confusing "did not define GeneratedActivity" message.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Provider code response did not include activity.py.')

    stripped = text.strip()

    # Detect model-side error messages that are not code.  Some
    # OpenRouter models return errors like "ERROR: Cannot read
    # image.png (this model does not support image input)" instead of
    # activity source.  These short, non-Python messages should be
    # surfaced to the user, not fed to ast.parse.
    if (stripped.startswith('ERROR:')
            or stripped.startswith('Error:')
            or stripped.startswith('ERROR ')):
        raise ValueError(
            'Model returned an error instead of activity code: %s'
            % stripped[:300]
        )

    try:
        value = extract_json_object(text)
    except ValueError:
        value = text
    if isinstance(value, dict):
        try:
            return extract_activity_source(value)
        except ValueError:
            # The "JSON" was an explanation object, or a dict literal
            # inside the code misread as the response wrapper. Fall back
            # to reading the raw text as fenced/plain code.
            return extract_activity_source(text)
    return extract_activity_source(value)


def _strip_code_fence(source):
    source = (source or '').strip()
    if '```' not in source:
        return source

    blocks = _fenced_blocks(source)
    if blocks:
        for block in blocks:
            if 'GeneratedActivity' in block:
                return block
        return max(blocks, key=len)

    # Unterminated fence (usually a truncated stream): keep everything
    # after the opening fence line so the caller can report a clear
    # truncation/syntax problem instead of "no activity.py".
    fence_start = source.find('```')
    first_newline = source.find('\n', fence_start)
    if first_newline >= 0:
        return source[first_newline + 1:].strip()
    return ''


def _fenced_blocks(source):
    """Return the contents of all complete ``` fenced blocks."""
    blocks = []
    index = 0
    while True:
        start = source.find('```', index)
        if start < 0:
            break
        first_newline = source.find('\n', start)
        if first_newline < 0:
            break
        end = source.find('```', first_newline + 1)
        if end < 0:
            break
        block = source[first_newline + 1:end].strip()
        if block:
            blocks.append(block)
        index = end + 3
    return blocks


def _format_references(references):
    # Prefer real activity.py source examples over manifest/pattern
    # blurbs -- the prompt tells the model to follow the GTK/Sugar
    # patterns in these references, which only works when it actually
    # sees code.
    code_docs = [
        document for document in references
        if str(getattr(document, 'source_path', '')).endswith('.py')
    ]
    other_docs = [
        document for document in references if document not in code_docs
    ]
    chosen = (code_docs + other_docs)[:2]

    blocks = []
    for index, document in enumerate(chosen, 1):
        raw = getattr(document, 'text', '')
        title = getattr(document, 'title', 'Reference')
        if document in code_docs:
            # Keep newlines and indentation: whitespace-flattened Python
            # is useless as a pattern reference.  Only collapse runs of
            # blank lines.
            text = re.sub(r'\n{3,}', '\n\n', raw).strip()
        else:
            text = ' '.join(raw.split())
        blocks.append(
            'Reference %d - %s:\n%s' % (index, title, text[:4000])
        )
    return '\n\n'.join(blocks)
