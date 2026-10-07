"""
translate_svcomp.py - Translate SV-COMP .c loops into QF-LIA Python.

Reliable enough strategy: walk the body char-by-char, maintaining a brace and
paren depth, splitting into top-level C statements. For each statement we
classify it as one of: {while, for, if, if-without-braces, assert, return,
assume, reach_error, decl, assign, ++, --, unknown}. Then we emit the
corresponding Python line.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Set, Tuple


INDENT = "    "


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    src = re.sub(r"//[^\n]*", "", src)
    return src


def _find_main_body(src: str) -> str:
    src = _strip_comments(src)
    m = re.search(r"int\s+main\s*\([^)]*\)\s*\{", src)
    if not m:
        return ""
    body_start = m.end()
    depth = 1
    i = body_start
    while i < len(src) and depth > 0:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return src[body_start : i - 1]


def _translate_expression(expr: str) -> str:
    expr = expr.strip()
    expr = re.sub(r"\b&&\b", " and ", expr)
    expr = re.sub(r"\b\|\|\b", " or ", expr)
    expr = re.sub(r"!([^=])", r" not \1", expr)
    expr = expr.replace(";", "")
    expr = re.sub(r"__VERIFIER_nondet_(?:uint|int)\s*\(\s*\)", "0", expr)
    # Translate hex literals 0x... into int(...).
    expr = re.sub(r"0[xX]([0-9A-Fa-f]+)", lambda m: str(int(m.group(0), 16)), expr)
    # Translate C suffixes like U, L, UL, LU, etc.
    expr = re.sub(r"([0-9]+)[uUlL]+", r"\1", expr)
    return expr


def _collect_declared(body: str) -> Set[str]:
    declared: Set[str] = set()
    for vm in re.finditer(
        r"\b(?:int|unsigned\s+int|long)\s+([a-zA-Z_][a-zA-Z0-9_,\s]*?)\s*(?:=|;|\n|\Z)",
        body,
    ):
        names = vm.group(1)
        for nm in names.split(","):
            nm = nm.strip().split("=")[0].strip()
            if nm and re.match(r"[a-zA-Z_]\w*", nm):
                declared.add(nm)
    return declared


def _split_top_level(body: str) -> List[str]:
    """Split body into top-level statements respecting (), {}, and at ;
    level 0. Continuation past ';' is treated as a new statement.
    Statements are returned WITHOUT a trailing ';' (already stripped).
    """
    out: List[str] = []
    buf: List[str] = []
    paren_depth = 0
    brace_depth = 0
    i = 0
    n = len(body)
    while i < n:
        c = body[i]
        if c == "(":
            paren_depth += 1
            buf.append(c)
        elif c == ")":
            paren_depth = max(0, paren_depth - 1)
            buf.append(c)
        elif c == "{":
            brace_depth += 1
            buf.append(c)
        elif c == "}":
            brace_depth -= 1
            buf.append(c)
        elif c == ";" and paren_depth == 0 and brace_depth == 0:
            stmt = "".join(buf).strip()
            buf = []
            if stmt:
                out.append(stmt)
        else:
            buf.append(c)
        i += 1
    leftover = "".join(buf).strip()
    if leftover:
        out.append(leftover)
    return out


def _find_block(body: str, header_re: str) -> Tuple[int, int] | None:
    """Find the first match of header_re at top level (paren & brace depth==0
    before the header), and return (start_of_block_body, end_exclusive).
    The match must have the form `<header> {`."""
    paren_depth = 0
    brace_depth = 0
    i = 0
    n = len(body)
    while i < n:
        c = body[i]
        if c == "(":
            paren_depth += 1
        elif c == ")":
            paren_depth = max(0, paren_depth - 1)
        elif c == "{":
            if paren_depth == 0 and brace_depth == 0:
                # Could be a top-level block start
                prefix = body[:i]
                m = re.match(r"\s*" + header_re + r"\s*\{", prefix, re.DOTALL)
                # The header must end at position i (i.e. last non-space char is ')' or identifier)
                # Re-match: check that the position i-1 corresponds to end of header
                pass
            brace_depth += 1
        elif c == "}":
            brace_depth -= 1
        i += 1
    return None


def _extract_block_at(body: str, brace_pos: int) -> Tuple[int, str]:
    """body[brace_pos] is '{'; return (end_exclusive, inner)."""
    assert body[brace_pos] == "{"
    depth = 1
    j = brace_pos + 1
    while j < len(body) and depth > 0:
        if body[j] == "{":
            depth += 1
        elif body[j] == "}":
            depth -= 1
        j += 1
    return j, body[brace_pos + 1 : j - 1]


def _find_brace_after_header(body: str, header_re: str) -> Tuple[int, int, str] | None:
    """Find first occurrence of header_re ending with `{` at top level.
    Return (header_end_index_inclusive_of_brace, block_end, inner)."""
    n = len(body)
    for m in re.finditer(r"(?<![a-zA-Z_0-9])" + header_re + r"\s*\{", body, re.DOTALL):
        brace_pos = m.end() - 1
        # Verify it's at top level: paren depth 0 and brace depth 0 before this
        prefix = body[: m.start()]
        pd, bd = 0, 0
        for c in prefix:
            if c == "(":
                pd += 1
            elif c == ")":
                pd = max(0, pd - 1)
            elif c == "{":
                bd += 1
            elif c == "}":
                bd = max(0, bd - 1)
        if pd == 0 and bd == 0:
            end, inner = _extract_block_at(body, brace_pos)
            return (brace_pos, end, inner)
    return None


def _find_brace_after_header_index(body: str, header_re: str, after_pos: int = 0) -> Tuple[int, int, str] | None:
    n = len(body)
    for m in re.finditer(r"(?<![a-zA-Z_0-9])" + header_re + r"\s*\{", body[after_pos:], re.DOTALL):
        brace_pos = after_pos + m.end() - 1
        prefix = body[: after_pos + m.start()]
        pd, bd = 0, 0
        for c in prefix:
            if c == "(":
                pd += 1
            elif c == ")":
                pd = max(0, pd - 1)
            elif c == "{":
                bd += 1
            elif c == "}":
                bd = max(0, bd - 1)
        if pd == 0 and bd == 0:
            end, inner = _extract_block_at(body, brace_pos)
            return (brace_pos, end, inner)
    return None


def _find_matching_paren(body: str, start: int) -> int:
    """body[start]=='('. Return position of matching ')'."""
    assert body[start] == "("
    depth = 1
    j = start + 1
    while j < len(body) and depth > 0:
        if body[j] == "(":
            depth += 1
        elif body[j] == ")":
            depth -= 1
        j += 1
    return j - 1


def _find_if_no_brace(body: str, after_pos: int) -> Tuple[int, str, int, str] | None:
    """Find an `if (<cond>) <single-stmt> [else <single-stmt>]` at top level.
    Returns (then_stmt_end, cond, else_stmt_end, else_stmt) where else_stmt==''
    if no else. The cond is already passed through _translate_expression.
    """
    n = len(body)
    # Find the first 'if' keyword at top level
    for m in re.finditer(r"\bif\s*\(", body[after_pos:]):
        if_pos = after_pos + m.start()
        # Verify top-level
        prefix = body[after_pos:if_pos]
        pd, bd = 0, 0
        for c in prefix:
            if c == "(": pd += 1
            elif c == ")": pd = max(0, pd - 1)
            elif c == "{": bd += 1
            elif c == "}": bd = max(0, bd - 1)
        if pd != 0 or bd != 0:
            continue
        # Read the condition
        j = if_pos + len("if")
        # j now points to '('
        if j >= n or body[j] != "(":
            continue
        # Find matching ')'
        depth = 1
        j += 1
        cond_start = j
        while j < n and depth > 0:
            if body[j] == "(":
                depth += 1
            elif body[j] == ")":
                depth -= 1
            j += 1
        cond_end = j - 1
        cond = body[cond_start:cond_end]
        cond = _translate_expression(cond)
        # Now skip whitespace
        k = j
        while k < n and body[k] in " \t\r\n":
            k += 1
        # If `{` next, this is handled by _find_brace_after_header_index
        if k < n and body[k] == "{":
            return None
        # Else: single statement up to next ';' at top level
        stmt_start = k
        pd, bd = 0, 0
        while k < n:
            c = body[k]
            if c == "(": pd += 1
            elif c == ")": pd = max(0, pd - 1)
            elif c == "{": bd += 1
            elif c == "}": bd = max(0, bd - 1)
            elif c == ";" and pd == 0 and bd == 0:
                break
            k += 1
        if k >= n:
            return None
        then_stmt = body[stmt_start:k]
        then_end = k + 1  # skip the ';'
        # Check for 'else'
        p = k + 1
        while p < n and body[p] in " \t\r\n":
            p += 1
        if p < n and body[p:p+4] == "else":
            p += 4
            while p < n and body[p] in " \t\r\n":
                p += 1
            # Single statement up to ';'
            ss = p
            pd, bd = 0, 0
            while p < n:
                c = body[p]
                if c == "(": pd += 1
                elif c == ")": pd = max(0, pd - 1)
                elif c == "{": bd += 1
                elif c == "}": bd = max(0, bd - 1)
                elif c == ";" and pd == 0 and bd == 0:
                    break
                p += 1
            else_stmt = body[ss:p]
            else_end = p + 1
            return (then_end, cond, else_end, else_stmt)
        return (then_end, cond, then_end, "")
    return None


# --- Top-level compile ------------------------------------------------------


def _compile_block(body: str, depth: int) -> List[str]:
    indent = INDENT * depth
    out: List[str] = []
    i = 0
    n = len(body)
    while i < n:
        # Skip whitespace
        while i < n and body[i] in " \t\r\n":
            i += 1
        if i >= n:
            break

        # 0) Variable declarations MUST be matched before while/for/if
        # because the if(...) regex would otherwise match the leading
        # `int/unsigned int/long` if we tried it first.
        m = re.match(r"\s*(?:int|unsigned\s+int|long)\s+([a-zA-Z_][a-zA-Z0-9_,\s=+\-*/%]*?)\s*;\s*", body[i:], re.DOTALL)
        if m:
            decls = m.group(1)
            parts: list[str] = []
            buf: list[str] = []
            pd = 0
            for c in decls:
                if c == "(": pd += 1; buf.append(c)
                elif c == ")": pd -= 1; buf.append(c)
                elif c == "," and pd == 0:
                    parts.append("".join(buf).strip())
                    buf = []
                else:
                    buf.append(c)
            if buf:
                parts.append("".join(buf).strip())
            for part in parts:
                if not part:
                    continue
                if "=" in part:
                    nm, rhs = part.split("=", 1)
                    out.append(f"{indent}{nm.strip()} = {_compile(rhs.strip())}")
                else:
                    out.append(f"{indent}# declared: {part.strip()} (kept as param)")
            i += m.end()
            continue

        # 1) while (...) { ... }
        found = _find_brace_after_header_index(body, r"while\s*\([^;]*?\)", i)
        if found and found[0] >= i:
            brace_pos, end, inner = found
            # Extract condition from header
            header_text = body[brace_pos - 200 if brace_pos > 200 else 0: brace_pos]
            wm = re.search(r"while\s*\(\s*(.*?)\s*\)\s*$", header_text, re.DOTALL)
            cond = _compile(wm.group(1)) if wm else "True"
            out.append(f"{indent}while {cond}:")
            out.extend(_compile_block(inner, depth + 1) or [f"{indent}    pass"])
            i = end
            continue

        # 2) for (init; cond; step) { ... }
        found = _find_brace_after_header_index(body, r"for\s*\([^;]*?;[^;]*?;[^;]*?\)", i)
        if found and found[0] >= i:
            brace_pos, end, inner = found
            header_text = body[brace_pos - 200 if brace_pos > 200 else 0: brace_pos]
            fm = re.search(r"for\s*\(\s*(.*?)\s*;\s*(.*?)\s*;\s*(.*?)\s*\)\s*$", header_text, re.DOTALL)
            if fm:
                init = _compile(fm.group(1)).strip()
                cond = _compile(fm.group(2)).strip()
                step = _compile(fm.group(3)).strip()
                if init:
                    out.append(f"{indent}{init}")
                out.append(f"{indent}while {cond}:")
                out.extend(_compile_block(inner, depth + 2) or [f"{indent}    pass"])
                if step:
                    out.append(f"{indent}    {step}  # for-step")
            i = end
            continue

        # 3) if (<cond>) { ... } [else { ... }]
        found = _find_brace_after_header_index(body, r"if\s*\([^;]*?\)", i)
        if found and found[0] >= i:
            brace_pos, end, inner = found
            header_text = body[brace_pos - 200 if brace_pos > 200 else 0: brace_pos]
            im = re.search(r"if\s*\(\s*(.*?)\s*\)\s*$", header_text, re.DOTALL)
            cond = _compile(im.group(1)) if im else "True"
            # Check for `else { ... }` right after `end`
            rest = body[end:].lstrip()
            else_inner = ""
            if rest.startswith("else"):
                m4 = re.match(r"else\s*\{", rest, re.DOTALL)
                if m4:
                    else_brace_pos = end + len(body[end:]) - len(rest) + m4.end() - 1
                    end_e, else_inner = _extract_block_at(body, else_brace_pos)
                    end = end_e
            if "reach_error" in inner or "abort" in inner:
                # `if (!cond) reach_error()` ⇒ assert cond
                if cond.startswith("not "):
                    out.append(f"{indent}assert {cond[4:]}")
                else:
                    out.append(f"{indent}assert {cond}")
            else:
                out.append(f"{indent}if ({cond}):")
                out.extend(_compile_block(inner, depth + 1) or [f"{indent}    pass"])
                if else_inner:
                    out.append(f"{indent}else:")
                    out.extend(_compile_block(else_inner, depth + 1) or [f"{indent}    pass"])
            i = end
            continue

        # 3b) if (<cond>) <single-stmt> [else <single-stmt>]  (no braces)
        nobr = _find_if_no_brace(body, i)
        if nobr and nobr[0] > i:
            then_end, cond, else_end, else_stmt = nobr
            if cond.startswith("not "):
                pos = cond[4:]
            else:
                pos = cond
            # Identify the then-stmt text: from after the ')' to the ';'
            close_paren = _find_matching_paren(body, body.find("(", i))
            semi = body.find(";", close_paren)
            then_stmt_text = body[close_paren + 1 : semi].strip()
            if "reach_error" in then_stmt_text or "abort" in then_stmt_text:
                out.append(f"{indent}assert {pos}")
            else:
                out.append(f"{indent}if ({cond}):")
                out.append(f"{indent}    {_compile(then_stmt_text)}")
            if else_stmt:
                out.append(f"{indent}else:")
                out.append(f"{indent}    {_compile(else_stmt.strip())}")
            i = else_end
            continue
        # 4) __VERIFIER_assert(<cond>);
        m = re.match(r"\s*__VERIFIER_assert\s*\(\s*(.+?)\s*\)\s*;", body[i:], re.DOTALL)
        if m:
            expr_p = _compile(m.group(1))
            # If the expression is not a comparison, wrap as `expr != 0`.
            if not re.search(r"(==|!=|<=|>=|<|>)", expr_p):
                expr_p = f"({expr_p}) != 0"
            out.append(f"{indent}assert {expr_p}")
            i += m.end()
            continue

        # 5) reach_error();abort();
        m = re.match(r"\s*reach_error\s*\(\s*\)\s*;\s*abort\s*\(\s*\)\s*;", body[i:])
        if m:
            out.append(f"{indent}assert False, \"reach_error\"")
            i += m.end()
            continue

        # 6) if (!cond) reach_error();abort();  (no braces)
        m = re.match(r"\s*if\s*\(\s*!\s*\(\s*(.+?)\s*\)\s*\)\s*reach_error\s*\(\s*\)\s*;\s*abort\s*\(\s*\)\s*;", body[i:], re.DOTALL)
        if m:
            out.append(f"{indent}assert {_compile(m.group(1))}")
            i += m.end()
            continue

        # 6b) if (!cond) return 0;  (single-line form)
        m = re.match(r"\s*if\s*\(\s*!\s*\(\s*(.+?)\s*\)\s*\)\s*return\s*[^;]*;\s*", body[i:], re.DOTALL)
        if m:
            cond = _compile(m.group(1))
            out.append(f"{indent}if not ({cond}):")
            out.append(f"{indent}    return")
            i += m.end()
            continue

        # 7) assume_abort_if_not
        m = re.match(r"\s*assume_abort_if_not\s*\(\s*(.+?)\s*\)\s*;", body[i:], re.DOTALL)
        if m:
            cond = _compile(m.group(1))
            out.append(f"{indent}if not ({cond}):")
            out.append(f"{indent}    return")
            i += m.end()
            continue

        # 8) return ...;
        m = re.match(r"\s*return\s*([^;]*?)\s*;\s*", body[i:])
        if m:
            out.append(f"{indent}return")
            i += m.end()
            continue

        # 11) Assignment
        m = re.match(r"\s*([a-zA-Z_]\w*)\s*=\s*([^;]+?)\s*;\s*", body[i:], re.DOTALL)
        if m:
            out.append(f"{indent}{m.group(1)} = {_compile(m.group(2))}")
            i += m.end()
            continue

        # 12) ++x / x++ / --x / x--
        m = re.match(r"\s*\+\+([a-zA-Z_]\w*)\s*;\s*", body[i:])
        if m:
            out.append(f"{indent}{m.group(1)} += 1"); i += m.end(); continue
        m = re.match(r"\s*([a-zA-Z_]\w*)\+\+\s*;\s*", body[i:])
        if m:
            out.append(f"{indent}{m.group(1)} += 1"); i += m.end(); continue
        m = re.match(r"\s*--([a-zA-Z_]\w*)\s*;\s*", body[i:])
        if m:
            out.append(f"{indent}{m.group(1)} -= 1"); i += m.end(); continue
        m = re.match(r"\s*([a-zA-Z_]\w*)--\s*;\s*", body[i:])
        if m:
            out.append(f"{indent}{m.group(1)} -= 1"); i += m.end(); continue

        # Skip unknown char
        i += 1
        out.append(f"{indent}# skip: {body[i-1]!r}")
    return out


def _compile(expr: str) -> str:
    return _translate_expression(expr)


# --- Entry point ------------------------------------------------------------


def translate_file(c_path: Path, py_path: Path) -> str:
    src = c_path.read_text(encoding="utf-8", errors="ignore")
    body = _find_main_body(src)
    if not body:
        raise ValueError(f"cannot extract main() in {c_path}")
    declared = _collect_declared(body)
    lines = _compile_block(body, depth=1)
    if not lines:
        lines = [f"{INDENT}pass"]
    params = ", ".join(sorted(declared))
    py_text = f"def f({params}):\n" + "\n".join(lines) + "\n"
    py_path.parent.mkdir(parents=True, exist_ok=True)
    py_path.write_text(py_text, encoding="utf-8")
    return py_text


def main(argv: List[str]) -> int:
    if len(argv) < 3:
        print("usage: translate_svcomp.py <input.c> <output.py>", file=sys.stderr)
        return 1
    out = translate_file(Path(argv[1]), Path(argv[2]))
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))