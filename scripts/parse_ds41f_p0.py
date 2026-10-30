#!/usr/bin/env python3
"""从 run_combined.jsonl 提取本批次 P0 失败/转人工明细（含 evidence 原始返回）"""
import glob
import json
import os
from pathlib import Path

files = sorted(glob.glob('artifacts/session/run_combined.jsonl'), key=os.path.getmtime)
f = files[-1]
lines = Path(f).read_text(encoding='utf-8').splitlines()

# 定位本批次起点：run_start 事件不带 model 字段（已知缺陷），
# 改用 ts 时间戳切分 —— 批次起始时间取自本轮 combined_report 启动时刻。
# 用「最后一个 data_loaded 之后」作为批次起点（每轮批次开始都会重新加载用例集）
dl = [i for i, l in enumerate(lines) if '"data_loaded"' in l]
start = dl[-1] if dl else 0
print(f"批次起点行号={start}（最后一个 data_loaded 事件）")

seg = lines[start:]
runs = {}
order = []
cur = None
cur_cid = None

for l in seg:
    try:
        e = json.loads(l)
    except json.JSONDecodeError:
        # 非 JSON 行（如日志混入）跳过，不中断整个批次解析
        continue
    ev = e.get('event')
    cid = e.get('case_id')
    if ev == 'run_start':
        # flush 上一个未结束（被下一 case 打断）
        if cur and cur_cid:
            cur['status'] = 'failed'
            cur['assertions'].append({
                'assertion': 'api_call', 'engine': 'deterministic', 'value': 0,
                'confidence': 1.0, 'status': 'auto_fail',
                'detail': '用例启动后未完成（被下一用例打断/超时）'})
            if cur_cid not in runs:
                order.append(cur_cid)
            runs[cur_cid] = cur
        cur = {'title': e.get('title'), 'status': None, 'assertions': [], 'ev': []}
        cur_cid = cid
        if cur_cid not in runs and cur_cid not in order:
            order.append(cur_cid)
    elif ev == 'assertion_judged' and cur is not None:
        cur['assertions'].append({
            'assertion': e.get('assertion'), 'engine': e.get('engine'),
            'value': e.get('value'), 'confidence': e.get('confidence'),
            'status': e.get('status'), 'detail': (e.get('detail') or '')[:200],
        })
        cur['ev'].append(e)
    elif ev == 'run_end' and cur is not None:
        cur['status'] = e.get('status')
        if cur_cid not in order:
            order.append(cur_cid)
        runs[cur_cid] = cur
        cur = None
        cur_cid = None

total = len(order)
passed = sum(1 for c in order if (runs.get(c) or {}).get('status') == 'passed')
print(f"本批次 P0: {passed}/{total} 通过 = {passed/total*100:.1f}%")
print(f"状态分布: passed={passed}, "
      f"failed={sum(1 for c in order if (runs.get(c) or {}).get('status')=='failed')}, "
      f"need_review={sum(1 for c in order if (runs.get(c) or {}).get('status')=='need_review')}")

# TTFT 汇总
ttfts = []
for c in order:
    for a in (runs.get(c) or {}).get('assertions', []):
        if 'ttft' in str(a.get('assertion')):
            ttfts.append((c, a))
if ttfts:
    print("\n=== TTFT 类断言 ===")
    for c, a in ttfts:
        print(f"  {c}: value={a['value']} conf={a['confidence']} status={a['status']} | {a['detail'][:110]}")

print("\n=== 非 passed 明细 ===")
for c in order:
    r = runs.get(c) or {}
    st = r.get('status')
    if st == 'passed':
        continue
    print(f"\n--- {c}  status={st}")
    print(f"    title: {r.get('title')}")
    for a in r.get('assertions', []):
        print(f"    [{a['status']}] {a['assertion']} | {a['engine']} | value={a['value']} | conf={a['confidence']}")
        print(f"           {a['detail']}")
    # 找最后一次 API 响应内容
    for e in r.get('ev', []):
        if 'response' in str(e.get('event', '')).lower() or e.get('llm_response'):
            print(f"    >>> 返回: {str(e.get('llm_response'))[:300]}")

out_path = Path('artifacts/ds41f_intra_p0_detail.json')
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(
    json.dumps({'order': order, 'runs': runs}, ensure_ascii=False, indent=2, default=str),
    encoding='utf-8',
)
print("\n明细已存 artifacts/ds41f_intra_p0_detail.json")