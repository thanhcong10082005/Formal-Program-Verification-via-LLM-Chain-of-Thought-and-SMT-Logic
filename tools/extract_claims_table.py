import json
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

with open('results/complex_experiment/all_complex_results.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print(f"{'Task':<28} | {'Claim Text':<25} | {'Line':<5} | {'AST Label':<16} | {'UN Label':<16} | {'Lý do / Phản ví dụ'}")
print("-" * 125)

for item in data:
    p_id = item['id']
    ast_res = item['ast_anchored']
    un_res = item['unanchored']
    
    for ca, cu in zip(ast_res['claim_results'], un_res['claim_results']):
        txt = ca['claim_text']
        if len(txt) > 23:
            txt = txt[:20] + "..."
        lines = str(ca['anchor']['source_lines'])
        st_ast = ca['status']
        st_un = cu['status']
        
        reason = ""
        if st_ast == "UNREACHABLE":
            reason = "Nhánh chết (Reachability UNSAT)"
        elif st_ast in ("UNSUPPORTED", "TRANSLATION_ERROR"):
            reason = ca['reason']
        elif ca.get('counterexample'):
            ce = ca['counterexample'].get('z3_model', {})
            reason = f"CE: {ce}"
        elif cu.get('counterexample'):
            ce = cu['counterexample'].get('z3_model', {})
            reason = f"CE: {ce}"
        else:
            reason = ca.get('reason', '')
            
        print(f"{p_id:<28} | {txt:<25} | {lines:<5} | {st_ast:<16} | {st_un:<16} | {reason}")
