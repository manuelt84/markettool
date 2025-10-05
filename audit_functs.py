#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import ast, argparse, hashlib, json, sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Set

@dataclass(frozen=True)
class FuncInfo:
    name: str          # nombre simple o Class.method si es método
    lineno: int
    end_lineno: int
    is_method: bool
    body_hash: str
    norm_src: str

def _strip_docstring(fn: ast.AST) -> ast.AST:
    node = ast.parse(ast.unparse(fn)).body[0]  # copia
    # elimina docstring inicial si existe
    if getattr(node, "body", None):
        if isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant) \
           and isinstance(node.body[0].value.value, str):
            node.body = node.body[1:]
    return node

def _hash_func(fn: ast.AST) -> Tuple[str, str]:
    try:
        node = _strip_docstring(fn)
        norm = ast.unparse(node)
    except Exception:
        norm = ast.dump(fn, include_attributes=False, annotate_fields=False)
    h = hashlib.sha1(norm.encode("utf-8")).hexdigest()
    return h, norm

def scan_single_file(path: Path, include_methods: bool):
    src = path.read_text(encoding="utf-8", errors="ignore")
    tree = ast.parse(src)

    funcs: List[FuncInfo] = []
    called_names: Set[str] = set()        # foo(...)
    called_attr_names: Set[str] = set()   # obj.foo(...), Class.foo(...)

    class CallVisitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                called_names.add(f.id)
            elif isinstance(f, ast.Attribute):
                called_attr_names.add(f.attr)
            self.generic_visit(node)

    CallVisitor().visit(tree)

    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            h, norm = _hash_func(n)
            funcs.append(FuncInfo(n.name, n.lineno, getattr(n, "end_lineno", n.lineno), False, h, norm))
        elif include_methods and isinstance(n, ast.ClassDef):
            for m in n.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    h, norm = _hash_func(m)
                    funcs.append(FuncInfo(f"{n.name}.{m.name}", m.lineno, getattr(m, "end_lineno", m.lineno), True, h, norm))

    # Duplicados por hash
    dup_map = {}
    for f in funcs:
        dup_map.setdefault(f.body_hash, []).append(f)
    duplicates = [group for group in dup_map.values() if len(group) > 1]

    # No usadas (heurístico)
    unused: List[FuncInfo] = []
    for f in funcs:
        simple = f.name.split(".")[-1]
        if f.is_method:
            used = (simple in called_attr_names) or (simple in called_names)
        else:
            used = (simple in called_names)
        if not used and not simple.startswith("_"):
            unused.append(f)

    return duplicates, unused

def print_text(duplicates, unused, file_path: str):
    print(f"\nAnalizando: {file_path}")
    print("\n=== Funciones duplicadas (cuerpo idéntico) ===")
    if not duplicates:
        print("Sin duplicados exactos.")
    else:
        for i, group in enumerate(duplicates, 1):
            print(f"\nGrupo #{i}  (x{len(group)})")
            for f in group:
                scope = "método" if f.is_method else "función"
                print(f" - {scope} {f.name}  [líneas {f.lineno}-{f.end_lineno}]")

    print("\n=== Funciones potencialmente no usadas (heurístico) ===")
    if not unused:
        print("Todas parecen usarse (o son privadas/_).")
    else:
        for f in unused:
            scope = "método" if f.is_method else "función"
            print(f" - {scope} {f.name}  [líneas {f.lineno}-{f.end_lineno}]")

def main():
    ap = argparse.ArgumentParser(description="Audita un único archivo .py (duplicados y no usadas).")
    ap.add_argument("--file", required=True, help="Ruta al archivo .py a analizar")
    ap.add_argument("--include-methods", action="store_true", help="Incluir métodos de clase")
    ap.add_argument("--report", choices=["text","json"], default="text")
    args = ap.parse_args()

    p = Path(args.file)
    if not p.exists():
        print(f"No existe: {p}", file=sys.stderr); sys.exit(2)

    duplicates, unused = scan_single_file(p, include_methods=args.include_methods)

    if args.report == "json":
        out = {
            "file": str(p),
            "duplicates": [
                [{"name": f.name, "lineno": f.lineno, "end_lineno": f.end_lineno, "is_method": f.is_method}
                 for f in group] for group in duplicates
            ],
            "unused": [{"name": f.name, "lineno": f.lineno, "end_lineno": f.end_lineno, "is_method": f.is_method}
                       for f in unused]
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print_text(duplicates, unused, str(p))

if __name__ == "__main__":
    main()
