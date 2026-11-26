import os

def attempt(name, fn):
    try:
        fn()
        print("ESCAPED", name)
    except OSError:
        print("BLOCKED", name)

def write(path):
    def go():
        with open(path, "w") as f:
            f.write("x")
    return go

attempt("write_root", write("/pwned"))
attempt("write_etc", write("/etc/pwned"))
attempt("write_usr", write("/usr/bin/pwned"))
attempt("write_tmp", write("/tmp/pwned"))
attempt("write_build", write("/workspace/build/pwned"))
attempt("write_src", write("/workspace/src/main.py"))
attempt("chmod_build", lambda: os.chmod("/workspace/build", 0o777))
attempt("read_shadow", lambda: open("/etc/shadow").read())
attempt("docker_socket", lambda: open("/var/run/docker.sock", "rb").read(1))
attempt("proc_1_environ", lambda: open("/proc/1/environ", "rb").read())
attempt("proc_1_root", lambda: os.listdir("/proc/1/root/root"))
def symlink_escape():
    os.symlink("/etc/shadow", "/workspace/run/link")
    open("/workspace/run/link").read()
attempt("symlink_shadow", symlink_escape)
def traversal():
    open("/workspace/run/../../../etc/shadow").read()
attempt("traversal_shadow", traversal)
