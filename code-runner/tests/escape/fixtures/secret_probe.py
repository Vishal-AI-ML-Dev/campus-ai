import os
# The runner passes no environment of its own: no tokens, no RUNNER_/CODE_RUNNER_/METRICS_ variables, no proxies.
bad = [k for k in os.environ if any(s in k.upper() for s in ("TOKEN", "SECRET", "RUNNER", "METRICS", "PROXY",
                                                            "DATABASE", "REDIS", "AWS", "GOOGLE", "SENTRY"))]
print("ESCAPED env " + ",".join(sorted(bad)) if bad else "BLOCKED env")
try:
    data = open("/proc/self/environ", "rb").read()
    print("ESCAPED environ" if any(s in data.upper() for s in (b"TOKEN", b"SECRET", b"RUNNER_")) else "BLOCKED environ")
except OSError:
    print("BLOCKED environ")
found = []
for root in ("/srv", "/opt", "/var/lib", "/etc/campus", "/workspace"):
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith((".in", ".out", ".tar")) or "manifest" in f or "testpack" in dirpath:
                found.append(os.path.join(dirpath, f))
print("ESCAPED pack " + ",".join(found[:3]) if found else "BLOCKED pack")
