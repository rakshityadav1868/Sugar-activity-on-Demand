# Copyright (C) 2026 Sugar Labs
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Deterministic repairs for unambiguous generated API mistakes."""

import ast
import re


_RAW_GTK_TOOL_BUTTONS = {
    'Gtk.RadioToolButton',
    'Gtk.ToggleToolButton',
    'Gtk.ToolButton',
}

_OS_PATH_EXISTS = re.compile(
    r'\bos\.path\.exists\(\s*'
    r'([A-Za-z_][A-Za-z0-9_.]*(?:\[[^\]\n]+\])?)'
    r'\s*\)'
)


def find_known_api_issues(source):
    """Return actionable errors for API mistakes we can identify exactly."""
    if not isinstance(source, str):
        return []

    issues = []
    if _uses_bare_cairo_api_without_import(source):
        issues.append(
            'The activity uses cairo.LinearGradient, cairo.RadialGradient, '
            'cairo.ImageSurface, or cairo.Context without importing cairo; '
            'add `import cairo` or the drawing callback will crash and leave '
            'the canvas blank.'
        )
    if re.search(r'\bstyle\.grid_size\b', source):
        issues.append(
            'sugar3.graphics.style has no grid_size attribute; use the '
            'uppercase style.GRID_CELL_SIZE constant.'
        )

    if re.search(r'(?m)^\s*self\.metadata\.save\s*\(\s*\)', source):
        issues.append(
            'Sugar activity metadata has no save() method; remove '
            '`self.metadata.save()`. Update metadata or the Journal file in '
            'write_file(), and let the Activity/Journal lifecycle persist it. '
            'Calling metadata.save() from a delayed win or level transition '
            'crashes the activity mid-game.'
        )

    if re.search(r'(?<![A-Za-z0-9_.])(?:cr|ctx|context)\.begin_new_path\s*\(',
                 source):
        issues.append(
            'cairo.Context has no begin_new_path() method; use new_path() '
            'or the draw callback will crash and leave the canvas blank.'
        )

    if re.search(
            r'(?m)^\s*(?:cr|ctx|context)\.[A-Za-z_][A-Za-z0-9_]*\s*=',
            source):
        issues.append(
            'A cairo.Context does not allow generated code to attach custom '
            'attributes or helper methods (for example `cr.ellipse = ...`); '
            'the draw callback will raise AttributeError later when that '
            'branch is reached. Use a normal helper function, or draw an '
            'ellipse with save(), translate(), scale(), arc(), restore(), '
            'then fill()/stroke().'
        )

    color_stop_issues = _invalid_cairo_color_stop_calls(source)
    if color_stop_issues:
        issues.append(
            'Cairo gradient color stops have the wrong number of arguments: '
            '%s. add_color_stop_rgb requires offset, red, green, blue '
            '(4 arguments); add_color_stop_rgba additionally requires alpha '
            '(5 arguments). This often crashes only when a delayed drawing '
            'branch such as a finish line or boss scene becomes visible.'
            % ', '.join(color_stop_issues)
        )

    raw_targets = _raw_gtk_tool_button_targets(source)
    if any(re.search(
            r'(?<![A-Za-z0-9_.])%s\.set_tooltip\s*\('
            % re.escape(target), source)
            for target in raw_targets):
        issues.append(
            'Raw Gtk.ToolButton, Gtk.ToggleToolButton, and '
            'Gtk.RadioToolButton widgets have no set_tooltip() method; use '
            'set_tooltip_text(), or use the corresponding sugar3 graphics '
            'tool button when it is activity chrome.'
        )
    return issues


def _invalid_cairo_color_stop_calls(source):
    """Describe pycairo gradient stop calls with an invalid arity."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, TypeError, ValueError):
        return []

    invalid = []
    expected = {
        'add_color_stop_rgb': 4,
        'add_color_stop_rgba': 5,
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or \
                not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr
        if method not in expected:
            continue
        # Pycairo exposes these as positional-only methods. Keywords are
        # invalid even when the combined count happens to look correct.
        if len(node.args) != expected[method] or node.keywords:
            invalid.append('%s at line %d (%d given, %d required)' % (
                method,
                getattr(node, 'lineno', 0),
                len(node.args) + len(node.keywords),
                expected[method],
            ))
    return invalid


def apply_known_api_repairs(source):
    """Return ``(source, repairs)`` after safe, localized corrections."""
    if not isinstance(source, str):
        return source, []

    repaired = source
    repairs = []

    repaired, metadata_save_count = re.subn(
        r'(?m)^\s*self\.metadata\.save\s*\(\s*\)\s*(?:#.*)?\n?',
        '', repaired)
    if metadata_save_count:
        repairs.append({
            'kind': 'sugar_metadata_save',
            'count': metadata_save_count,
            'replacement': '',
        })

    if _uses_bare_cairo_api_without_import(repaired):
        repaired = _add_plain_import(repaired, 'cairo')
        repairs.append({
            'kind': 'missing_cairo_import',
            'count': 1,
            'replacement': 'import cairo',
        })

    repaired, cairo_path_count = re.subn(
        r'(?<![A-Za-z0-9_.])(?P<target>cr|ctx|context)'
        r'\.begin_new_path(?P<call>\s*\()',
        r'\g<target>.new_path\g<call>',
        repaired,
    )
    if cairo_path_count:
        repairs.append({
            'kind': 'cairo_begin_new_path',
            'count': cairo_path_count,
            'replacement': 'new_path',
        })

    grid_size_count = len(re.findall(r'\bstyle\.grid_size\b', repaired))
    if grid_size_count:
        repaired = re.sub(
            r'\bstyle\.grid_size\b', 'style.GRID_CELL_SIZE', repaired)
        repairs.append({
            'kind': 'sugar_style_constant',
            'count': grid_size_count,
            'replacement': 'style.GRID_CELL_SIZE',
        })

    tooltip_count = 0
    for target in sorted(
            _raw_gtk_tool_button_targets(repaired), key=len, reverse=True):
        pattern = (
            r'(?<![A-Za-z0-9_.])(%s)\.set_tooltip(\s*\()'
            % re.escape(target)
        )
        repaired, count = re.subn(
            pattern, r'\1.set_tooltip_text\2', repaired)
        tooltip_count += count
    if tooltip_count:
        repairs.append({
            'kind': 'raw_gtk_tooltip_method',
            'count': tooltip_count,
            'replacement': 'set_tooltip_text',
        })

    # Generated Journal readers commonly add ``os.path.exists(file_path)``
    # even though ``os`` is intentionally forbidden in activity.py.  GLib's
    # local file test is the exact GTK-safe equivalent.  Only commit the
    # deterministic repair when every executable use of ``os`` disappears;
    # any broader filesystem dependency still belongs in the model repair
    # path instead of being partially rewritten here.
    os_candidate, exists_count = _OS_PATH_EXISTS.subn(
        r'GLib.file_test(\1, GLib.FileTest.EXISTS)', repaired)
    if exists_count and not _has_loaded_name(os_candidate, 'os'):
        os_candidate, import_count = _remove_plain_import(
            os_candidate, 'os')
        if import_count:
            if not _has_repository_import(os_candidate, 'GLib'):
                os_candidate = _add_repository_import(
                    os_candidate, 'GLib')
            repaired = os_candidate
            repairs.append({
                'kind': 'journal_file_exists',
                'count': exists_count,
                'replacement': 'GLib.file_test',
            })
            repairs.append({
                'kind': 'forbidden_os_import',
                'count': import_count,
                'replacement': '',
            })

    return repaired, repairs


def _uses_bare_cairo_api_without_import(source):
    """Detect the exact blank-canvas failure caused by missing pycairo."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, TypeError, ValueError):
        return False

    uses_api = any(
        isinstance(node, ast.Attribute) and
        isinstance(node.value, ast.Name) and node.value.id == 'cairo' and
        node.attr in ('Context', 'ImageSurface', 'LinearGradient',
                      'RadialGradient')
        for node in ast.walk(tree)
    )
    if not uses_api:
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == 'cairo' and (alias.asname or 'cairo') == \
                        'cairo':
                    return False
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == 'cairo' and (alias.asname or 'cairo') == \
                        'cairo':
                    return False
    return True


def _add_plain_import(source, module):
    line = 'import %s\n' % module
    first_import = re.search(r'(?m)^(?:import|from)\s+', source)
    if first_import is not None:
        return source[:first_import.start()] + line + source[first_import.start():]
    return line + source


def _has_loaded_name(source, name):
    try:
        tree = ast.parse(source)
    except (SyntaxError, TypeError, ValueError):
        return True
    return any(
        isinstance(node, ast.Name) and node.id == name and
        isinstance(node.ctx, ast.Load)
        for node in ast.walk(tree)
    )


def _remove_plain_import(source, module):
    count = 0
    pattern = re.compile(
        r'(?m)^(?P<indent>[ \t]*)import[ \t]+'
        r'(?P<items>[^\n#]+?)(?P<comment>[ \t]*#.*)?$')

    def replace(match):
        nonlocal count
        items = [item.strip() for item in match.group('items').split(',')]
        kept = []
        for item in items:
            import_parts = item.split(' as ', 1)
            imported_name = import_parts[0].strip()
            if imported_name == module and len(import_parts) == 1:
                count += 1
            else:
                kept.append(item)
        if len(kept) == len(items):
            return match.group(0)
        if not kept:
            return ''
        comment = match.group('comment') or ''
        return '%simport %s%s' % (
            match.group('indent'), ', '.join(kept), comment)

    return pattern.sub(replace, source), count


def _has_repository_import(source, name):
    try:
        tree = ast.parse(source)
    except (SyntaxError, TypeError, ValueError):
        return False
    return any(
        isinstance(node, ast.ImportFrom) and
        node.module == 'gi.repository' and
        any(alias.name == name and alias.asname in (None, name)
            for alias in node.names)
        for node in ast.walk(tree)
    )


def _add_repository_import(source, name):
    repository_import = re.search(
        r'(?m)^from gi\.repository import [^\n]+\n?', source)
    line = 'from gi.repository import %s\n' % name
    if repository_import is not None:
        return source[:repository_import.start()] + line + \
            source[repository_import.start():]

    gi_import = re.search(r'(?m)^import gi[^\n]*\n?', source)
    if gi_import is not None:
        return source[:gi_import.end()] + line + source[gi_import.end():]
    return line + source


def _raw_gtk_tool_button_targets(source):
    try:
        tree = ast.parse(source)
    except (SyntaxError, TypeError, ValueError):
        return set()

    targets = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            value = node.value
            assignment_targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            value = node.value
            assignment_targets = (node.target,)
        else:
            continue
        if not isinstance(value, ast.Call) or \
                _expression_name(value.func) not in _RAW_GTK_TOOL_BUTTONS:
            continue
        for target in assignment_targets:
            name = _expression_name(target)
            if name:
                targets.add(name)
    return targets


def _expression_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _expression_name(node.value)
        return '%s.%s' % (parent, node.attr) if parent else node.attr
    return ''
