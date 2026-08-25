import urllib.request, re

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    return urllib.request.urlopen(req, timeout=20).read().decode('utf-8', 'replace')

try:
    html = fetch('https://dhan.freshdesk.com/support/solutions/articles/82000900258/')
    text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))
    print(text[:1800])
except Exception as e:
    print('ERR', e)
