
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pymupdf
src = r'C:\Users\tgowd\Downloads'
out = r'C:\PrOxyTradingTerminal\tools\_books'
os.makedirs(out, exist_ok=True)
books = [
    'Module 2_Technical Analysis.pdf',
    'Module 5_Options-Theory-for-Professional-Trading.pdf',
    'Module 6_Option Strategies.pdf',
    'Module 10_Trading Systems.pdf',
    'My Learnings - High probability trading strategies.pdf',
]
for b in books:
    p = os.path.join(src, b)
    if not os.path.exists(p):
        print('MISSING', b, flush=True); continue
    doc = pymupdf.open(p)
    texts = []
    for i in range(doc.page_count):
        t = doc[i].get_text().strip()
        texts.append(f'--- PAGE {i+1} ---\n' + t if t else f'--- PAGE {i+1} (no text) ---')
    name = re.sub(r'[^A-Za-z0-9]+', '_', b).strip('_')
    fn = os.path.join(out, name + '.txt')
    with open(fn, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(texts))
    withtext = sum(1 for t in texts if 'no text' not in t and len(t) > 60)
    print(b, '| pages', doc.page_count, '| with-text', withtext, '->', os.path.basename(fn), flush=True)
