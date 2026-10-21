"""The not-a-clone analytics core.

Pure functions over list[dict] rows. No pandas required. Four pieces:
  safe_eval    sandboxed AST expression evaluator for computed columns
  apply_computed   adds user formula columns
  apply_stats      adds zscore / pctrank / rank / norm virtual columns
  apply_factor     weighted multi-factor composite score and ranking
"""

from __future__ import annotations

import ast
import math
from typing import Any

# Functions a computed expression is allowed to call. Nothing else.
_ALLOWED_FUNCS = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "sqrt": math.sqrt,
    "log": math.log10,
    "ln": math.log,
    "floor": math.floor,
    "ceil": math.ceil,
}

# AST node types we permit. Everything else is rejected.
_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Pow,
    ast.FloorDiv,
    ast.USub,
    ast.UAdd,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Call,
)


# Bounds on exponentiation and expression size. Without them "9**9**9**9" pins a
# core and takes the whole server down with it.
_MAX_EXPONENT = 64
_MAX_POW_BASE = 1e9
_MAX_EXPR_LEN = 500


class _SafetyError(ValueError):
    """Raised when an expression contains a disallowed construct."""


def _check(node: ast.AST) -> None:
    """Recursively assert every node in the tree is on the allow list."""
    if not isinstance(node, _ALLOWED_NODES):
        raise _SafetyError("unsafe expression")

    if isinstance(node, ast.Name):
        if node.id.startswith("__") or "__" in node.id:
            raise _SafetyError("unsafe expression")

    if isinstance(node, ast.Constant):
        # Only numbers are allowed as literals. No strings, bytes, etc.
        if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
            raise _SafetyError("unsafe expression")

    if isinstance(node, ast.Call):
        # The callable must be a bare allowed-function name.
        if not isinstance(node.func, ast.Name):
            raise _SafetyError("unsafe expression")
        if node.func.id not in _ALLOWED_FUNCS:
            raise _SafetyError("unsafe expression")
        if node.keywords:
            raise _SafetyError("unsafe expression")

    for child in ast.iter_child_nodes(node):
        _check(child)


def _to_number(value: Any) -> float | None:
    """Coerce a row value to float, or None when not numeric."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _eval_node(node: ast.AST, row: dict) -> Any:
    """Evaluate a vetted AST node against a row dict."""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, row)

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        val = _to_number(row.get(node.id))
        if val is None:
            raise ZeroDivisionError("missing value")
        return val

    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, row)
        if isinstance(node.op, ast.USub):
            return -operand
        return +operand

    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, row)
        right = _eval_node(node.right, row)
        op = node.op
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            if right == 0:
                raise ZeroDivisionError("div by zero")
            return left / right
        if isinstance(op, ast.Mod):
            if right == 0:
                raise ZeroDivisionError("mod by zero")
            return left % right
        if isinstance(op, ast.FloorDiv):
            if right == 0:
                raise ZeroDivisionError("floordiv by zero")
            return left // right
        if isinstance(op, ast.Pow):
            # Unbounded exponentiation pins a core ("9**9**9**9"), so cap both
            # sides and reject any result that is not a sane float.
            if abs(right) > _MAX_EXPONENT or abs(left) > _MAX_POW_BASE:
                raise _SafetyError("exponent out of range")
            return left ** right
        raise _SafetyError("unsafe expression")

    if isinstance(node, ast.Call):
        func = _ALLOWED_FUNCS[node.func.id]
        args = [_eval_node(a, row) for a in node.args]
        return func(*args)

    raise _SafetyError("unsafe expression")


def safe_eval(expr: str, row: dict) -> float | None:
    """Evaluate expr against row in a sandbox.

    Returns a number, or None when a value is missing or division by zero
    occurs. Raises ValueError("unsafe expression") for disallowed syntax.
    """
    if len(expr) > _MAX_EXPR_LEN:
        raise _SafetyError("expression too long")
    tree = ast.parse(expr, mode="eval")
    _check(tree)
    try:
        result = _eval_node(tree, row)
    except ZeroDivisionError:
        return None
    if isinstance(result, bool) or not isinstance(result, (int, float)):
        return None
    if isinstance(result, float) and (math.isnan(result) or math.isinf(result)):
        return None
    return result


def apply_computed(rows: list[dict], computed: list[dict]) -> list[dict]:
    """Add each computed column to every row. Errors land as None."""
    for spec in computed or []:
        cid = spec["id"]
        expr = spec["expr"]
        for row in rows:
            try:
                row[cid] = safe_eval(expr, row)
            except ValueError:
                # Unsafe expression: leave the cell empty rather than crash.
                row[cid] = None
    return rows


def _column_values(rows: list[dict], field: str) -> list[float | None]:
    """Pull a column as floats, preserving None gaps."""
    return [_to_number(r.get(field)) for r in rows]


def _mean_std(nums: list[float]) -> tuple[float, float]:
    """Population mean and standard deviation."""
    n = len(nums)
    mean = sum(nums) / n
    var = sum((x - mean) ** 2 for x in nums) / n
    return mean, math.sqrt(var)


def _median(nums: list[float]) -> float:
    """Median of a non-empty sorted or unsorted list."""
    s = sorted(nums)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _quantile(sorted_nums: list[float], q: float) -> float:
    """Quantile q in [0,1] from a sorted list via linear interpolation."""
    n = len(sorted_nums)
    if n == 1:
        return sorted_nums[0]
    pos = q * (n - 1)
    lo = int(pos)
    hi = lo + 1
    if hi >= n:
        return sorted_nums[-1]
    return sorted_nums[lo] + (pos - lo) * (sorted_nums[hi] - sorted_nums[lo])


def apply_stats(rows: list[dict], stats: list[dict]) -> list[dict]:
    """Add zscore / pctrank / rank / norm virtual columns.

    Column name is exactly f"{fn}({field})". None values are ignored in
    the math and stay None in the output.
    """
    for spec in stats or []:
        fn = spec["fn"]
        field = spec["field"]
        colname = f"{fn}({field})"
        values = _column_values(rows, field)
        present = [v for v in values if v is not None]

        if not present:
            for row in rows:
                row[colname] = None
            continue

        if fn == "zscore":
            mean, std = _mean_std(present)
            for row, v in zip(rows, values):
                row[colname] = None if v is None else (
                    0.0 if std == 0 else round((v - mean) / std, 4)
                )

        elif fn == "pctrank":
            n = len(present)
            for row, v in zip(rows, values):
                if v is None:
                    row[colname] = None
                else:
                    below = sum(1 for x in present if x < v)
                    equal = sum(1 for x in present if x == v)
                    pr = (below + 0.5 * equal) / n * 100.0
                    row[colname] = round(pr, 2)

        elif fn == "rank":
            # Dense desc rank: largest value is rank 1.
            order = sorted(set(present), reverse=True)
            rank_of = {val: i + 1 for i, val in enumerate(order)}
            for row, v in zip(rows, values):
                row[colname] = None if v is None else rank_of[v]

        elif fn == "norm":
            lo, hi = min(present), max(present)
            span = hi - lo
            for row, v in zip(rows, values):
                row[colname] = None if v is None else (
                    0.0 if span == 0 else round((v - lo) / span, 4)
                )

        elif fn == "madzscore":
            # Robust z-score: (x - median) / (1.4826 * MAD).
            # MAD = median(|x - median|). The 1.4826 factor makes this
            # consistent with std for normally distributed data.
            # Much less sensitive to outliers than a standard z-score.
            med = _median(present)
            mad = _median([abs(x - med) for x in present])
            scale = 1.4826 * mad
            for row, v in zip(rows, values):
                row[colname] = None if v is None else (
                    0.0 if scale == 0 else round((v - med) / scale, 4)
                )

        elif fn == "winsor":
            # Winsorized normalization: clip values at the 5th/95th percentile
            # then normalize the clipped column to [0, 1]. Keeps every row
            # in the result set while damping the distortion extreme outliers
            # cause in min-max normalization. Ideal for factor scoring.
            s = sorted(present)
            lo_clip = _quantile(s, 0.05)
            hi_clip = _quantile(s, 0.95)
            span = hi_clip - lo_clip
            for row, v in zip(rows, values):
                if v is None:
                    row[colname] = None
                else:
                    clipped = max(lo_clip, min(hi_clip, v))
                    row[colname] = 0.0 if span == 0 else round((clipped - lo_clip) / span, 4)

        elif fn == "decile":
            # Decile 1-10: 1 is the lowest 10%, 10 is the highest 10%.
            # Boundaries are set at the 10th, 20th, ..., 90th percentile via
            # linear interpolation, so ties near boundaries fall into the lower
            # decile consistently. Useful for bucketing factor scores.
            s = sorted(present)
            breaks = [_quantile(s, k / 10.0) for k in range(1, 10)]
            for row, v in zip(rows, values):
                if v is None:
                    row[colname] = None
                else:
                    d = 1
                    for b in breaks:
                        if v > b:
                            d += 1
                    row[colname] = d

        else:
            for row in rows:
                row[colname] = None

    return rows


def apply_factor(rows: list[dict], weights: list[dict]) -> list[dict]:
    """Composite weighted multi-factor score, written to row['factor_score'].

    weights = [{"field","weight","dir"}], dir in {"high","low"}. Each field
    is z-scored across rows, negated when dir=="low", scaled by weight, and
    summed. Missing values contribute 0 for that field.
    """
    if not weights:
        return rows

    # Precompute z-scores per field so we only walk each column once.
    zmaps: list[tuple[float, str, dict[int, float]]] = []
    for w in weights:
        field = w["field"]
        weight = float(w.get("weight", 1))
        direction = w.get("dir", "high")
        values = _column_values(rows, field)
        present = [v for v in values if v is not None]
        zmap: dict[int, float] = {}
        if present:
            mean, std = _mean_std(present)
            for i, v in enumerate(values):
                if v is None or std == 0:
                    zmap[i] = 0.0
                else:
                    z = (v - mean) / std
                    if direction == "low":
                        z = -z
                    zmap[i] = z
        zmaps.append((weight, field, zmap))

    for i, row in enumerate(rows):
        score = 0.0
        for weight, _field, zmap in zmaps:
            score += weight * zmap.get(i, 0.0)
        row["factor_score"] = round(score, 3)

    return rows
