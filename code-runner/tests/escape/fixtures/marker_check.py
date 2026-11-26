import os
seen = [p for p in ("/workspace/run/campus-marker", "/workspace/campus-marker") if os.path.exists(p)]
print("ESCAPED marker " + ",".join(seen) if seen else "BLOCKED marker")
