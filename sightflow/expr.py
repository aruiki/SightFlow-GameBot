"""Small, safe expression language for flow conditions.

It is a whitelisted subset of Python expressions, e.g.

    enemy.any and enemy.nearest.distance < 200
    coin.count >= 2 or vars.hunting
    bush.within(150) > 0 and state_time > 1.5

No imports, no dunder access, no arbitrary calls.
"""
from __future__ import annotations

import ast
from typing import Any

_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.FloorDiv,
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.Name, ast.Load, ast.Attribute, ast.Constant, ast.Call, ast.Tuple, ast.List, ast.IfExp,
)
_FUNCS = {"min": min, "max": max, "abs": abs, "len": len}
_METHODS = {"within"}


class ExprError(ValueError):
    pass


class Expr:
    def __init__(self, source: Any):
        self.source = str(source)
        try:
            tree = ast.parse(self.source.strip(), mode="eval")
        except SyntaxError as e:
            raise ExprError(f"bad expression {self.source!r}: {e.msg}") from e
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED_NODES):
                raise ExprError(f"{type(node).__name__} not allowed in {self.source!r}")
            if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
                raise ExprError(f"private attribute access not allowed in {self.source!r}")
            if isinstance(node, ast.Call):
                f = node.func
                ok = (isinstance(f, ast.Name) and f.id in _FUNCS) or (
                    isinstance(f, ast.Attribute) and f.attr in _METHODS
                )
                if not ok or node.keywords:
                    raise ExprError(f"call not allowed in {self.source!r}")
        self._code = compile(tree, "<flow>", "eval")

    def __call__(self, names: dict[str, Any]) -> Any:
        return eval(self._code, {"__builtins__": {}, **_FUNCS}, names)

    def __repr__(self) -> str:
        return f"Expr({self.source!r})"
