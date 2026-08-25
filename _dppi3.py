import urllib.request, re, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    return urllib.request.urlopen(req, timeout=20).read().decode('utf-8', 'replace')

html = fetch('https://dhan.freshdesk.com/support/solutions/articles/82000900258/')
text = re.sub(r'<[^>]+>', ' ', html)
text = re.sub(r'\s+', ' ', text)
i = text.find('There are two ways to activate')
print(text[i:i+2000])
