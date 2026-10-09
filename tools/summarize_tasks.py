import json

with open('results/mbpp_experiment/all_results.json', encoding='utf-8') as f:
    data = json.load(f)

for i, t in enumerate(data, 1):
    print(f"=== [{i}/10] Task {t['task_id']}: {t['name']} ===")
    print("Description:", t['description'])
    print("Code:\n" + t['code'].strip())
    print("Claims count:", t['anchored']['n_claims'])
    for c in t['anchored']['claim_results']:
        anchor_nodes = c['anchor'].get('node_ids', [])
        status = c['status']
        print(f"  - Claim: '{c['claim_text']}' -> Status: {status} (Nodes: {anchor_nodes})")
        if status == 'COUNTEREXAMPLE' and 'counterexample' in c and c['counterexample']:
            print(f"    CE model: {c['counterexample'].get('z3_model')}")
    print()

