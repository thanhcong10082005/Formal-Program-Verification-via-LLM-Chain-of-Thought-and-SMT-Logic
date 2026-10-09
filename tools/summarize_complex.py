import json

with open('results/complex_experiment/all_complex_results.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print(f"Total test cases: {len(data)}")
for item in data:
    p_id = item['id']
    cat = item['category']
    ast_res = item['ast_anchored']
    un_res = item['unanchored']
    print(f"\n[{p_id}] ({cat})")
    print(f"   AST: V={ast_res['n_verified']}, CE={ast_res['n_counterexample']}, UN={ast_res['n_unreachable']}, UNSUP={ast_res['n_unsupported'] + ast_res['n_translation_error']}")
    print(f"   UN : V={un_res['n_verified']}, CE={un_res['n_counterexample']}, UN={un_res['n_unreachable']}, UNSUP={un_res['n_unsupported'] + un_res['n_translation_error']}")
    for ca in ast_res['claim_results'][:3]:
        print(f"     Claim: '{ca['claim_text']}' -> AST: {ca['status']} | Anchor: {ca['anchor'].get('node_ids', [])}")
    for cu in un_res['claim_results'][:3]:
        print(f"     Claim: '{cu['claim_text']}' -> UN : {cu['status']} | Reason: {cu['reason']}")

