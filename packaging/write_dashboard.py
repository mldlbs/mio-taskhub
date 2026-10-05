"""Write dashboard.html to web/dist/"""
import sys
sys.path.insert(0, "E:/work/code/agent-dev/mio-taskhub")

html = open("E:/work/code/agent-dev/mio-taskhub/web/dist/dashboard.html", "r", encoding="utf-8").read()
print(f"Dashboard exists: {len(html)} bytes")
