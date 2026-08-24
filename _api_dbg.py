import urllib.request, urllib.error
for path in ["/api/mode", "/api/board", "/dashboard.html", "/"]:
    try:
        r = urllib.request.urlopen("http://127.0.0.1:8090" + path, timeout=5)
        print(path, "->", r.status, r.read(120)[:120])
    except urllib.error.HTTPError as e:
        print(path, "-> HTTP", e.code, e.read(200)[:200])
    except Exception as e:
        print(path, "-> ERR", e)
