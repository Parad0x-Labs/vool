"""Reviewed public API inventory and executable gate reachability checks.

The inventory is deliberately static: adding a module or public API requires a
classification. Memory modules outside core/ (the storage memory stores) are
inventoried on the same terms, and a public callable defined by assignment is a
public callable: lambdas bound to a module-level name are classified exactly
like def-formed readers. Reader entries must retain a call path to the common
serving boundary. Behavioral denial tests complement this structural check; a
call graph alone cannot establish that every branch in a function is safe.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

_EXTRA_MODULES = (
    'core/bootstrap_context.py', 'core/context_retrieval.py',
    'core/evidence_compiler.py', 'core/agent_runtime/same_chat_recall.py',
)
_CLASSIFICATIONS = json.loads(Path(__file__).with_suffix('.json').read_text())


def _module_paths(root):
    return sorted({*root.glob('core/**/*memory*.py'), *root.glob('core/memory/*.py'),
                   *root.glob('storage/*memory*.py'),
                   *(root / path for path in _EXTRA_MODULES)})


#: Assignment value shapes that can never produce a callable. Every other
#: public module-level assignment (an alias, an annotated assignment, a
#: factory call such as functools.partial(...)) is inventoried as a public
#: callable and demands a classification; an assignment whose value shape
#: cannot be proven a literal is rejected as unclassified.
_ASSIGNMENT_NON_CALLABLES = (ast.Constant, ast.List, ast.Tuple, ast.Dict,
                             ast.Set, ast.ListComp, ast.DictComp, ast.SetComp,
                             ast.GeneratorExp, ast.JoinedStr, ast.UnaryOp,
                             ast.BinOp, ast.Compare, ast.BoolOp)


def _definitions(tree):
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = node
        elif isinstance(node, ast.ClassDef) and not node.name.startswith('_'):
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[node.name + '.' + method.name] = method
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            value = node.value
            if value is None or isinstance(value, _ASSIGNMENT_NON_CALLABLES):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    out.setdefault(target.id, value)
    return out


def _public(name):
    return all(not part.startswith('_') for part in name.split('.'))


def _audit(root, *, source_overrides=None):
    """Return unclassified APIs and readers with no serving gate in their call path."""
    sources = {}
    for path in _module_paths(root):
        relative = path.relative_to(root).as_posix()
        source = (source_overrides or {}).get(relative, path.read_text())
        sources[relative] = _definitions(ast.parse(source))
    errors = []
    if sources.keys() != _CLASSIFICATIONS.keys():
        errors.append('memory module inventory changed')
    nodes = {file + ':' + name: node for file, definitions in sources.items() for name, node in definitions.items()}
    by_name = {}
    for key in nodes:
        by_name.setdefault(key.rsplit('.', 1)[-1].split(':')[-1], set()).add(key)
    graph = {}
    for key, node in nodes.items():
        # A def (or lambda) reaches its callees through call positions only; an
        # alias or factory assignment IS the callable it names, so every name
        # it references is a call path candidate.
        value_expression = not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        names = set()
        for item in ast.walk(node):
            value = item.func if isinstance(item, ast.Call) else None
            if isinstance(value, ast.Name):
                names.add(value.id)
            elif isinstance(value, ast.Attribute):
                names.add(value.attr)
            if value_expression:
                if isinstance(item, ast.Name):
                    names.add(item.id)
                elif isinstance(item, ast.Attribute):
                    names.add(item.attr)
        for decorator in getattr(node, 'decorator_list', ()):
            if isinstance(decorator, ast.Name):
                names.add(decorator.id)
        graph[key] = {target for name in names for target in by_name.get(name, ())}
    terminal = 'core/memory/revocation.py:authorize_payload'

    def gated(key, seen):
        if key == terminal:
            return True
        if key in seen:
            return False
        seen.add(key)
        return any(gated(target, seen) for target in graph.get(key, ()))

    for file, definitions in sources.items():
        classified = _CLASSIFICATIONS.get(file, {})
        public = {name for name in definitions if _public(name)}
        if public != classified.keys():
            errors.append(file + ': unclassified/stale APIs ' + repr(sorted(public ^ classified.keys())))
        for name, kind in classified.items():
            if kind not in {'reader', 'writer', 'authority', 'transform', 'configuration', 'diagnostic', 'orchestrator'}:
                errors.append(file + ':' + name + ': unknown classification')
            if kind in {'reader', 'orchestrator'} and not gated(file + ':' + name, set()):
                errors.append(file + ':' + name + ': ungated reader')
    return errors
