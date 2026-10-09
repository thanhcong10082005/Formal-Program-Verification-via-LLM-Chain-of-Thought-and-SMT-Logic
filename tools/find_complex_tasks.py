import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from astverifier.subset import check_and_normalize

mbpp_file = Path('E:/BTL/Dataset/mbpp/mbpp.jsonl')
with open(mbpp_file, 'r', encoding='utf-8') as f:
    lines = [json.loads(line) for line in f]

passed_tasks = []
for item in lines:
    code = item['code']
    res = check_and_normalize(code)
    if res.accepted:
        # Check if it has while loops or multiple branches
        n_lines = len(code.strip().splitlines())
        has_while = 'while' in code
        num_ifs = code.count('if ')
        passed_tasks.append({
            'task_id': item['task_id'],
            'text': item['text'],
            'code': code,
            'lines': n_lines,
            'has_while': has_while,
            'num_ifs': num_ifs
        })

print(f"Total QF-LIA valid tasks in MBPP: {len(passed_tasks)}")

# Sort by complexity: has_while or high num_ifs, longer code
passed_tasks.sort(key=lambda x: (x['has_while'], x['num_ifs'], x['lines']), reverse=True)

for t in passed_tasks[:10]:
    print(f"Task ID {t['task_id']} | Lines: {t['lines']} | while: {t['has_while']} | ifs: {t['num_ifs']}")
    print("Prompt:", t['text'])
    print("Code:\n" + t['code'].strip())
    print("-" * 50)
