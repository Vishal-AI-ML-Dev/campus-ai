#define _GNU_SOURCE
#include <sched.h>
#include <stdio.h>
#include <sys/mount.h>
#include <sys/ptrace.h>
#include <sys/types.h>
#include <unistd.h>

static void report(const char *name, int failed) { printf("%s %s\n", failed ? "BLOCKED" : "ESCAPED", name); }

int main(void) {
    report("setuid_root", setuid(0) != 0);
    report("setgid_root", setgid(0) != 0);
    report("ptrace_attach_pid1", ptrace(PTRACE_ATTACH, 1, 0, 0) != 0);
    report("mount_tmpfs", mount("none", "/workspace/run", "tmpfs", 0, "") != 0);
    report("unshare_mount_ns", unshare(CLONE_NEWNS) != 0);
    report("unshare_net_ns", unshare(CLONE_NEWNET) != 0);
    report("chroot", chroot("/") != 0);
    report("still_uid_65534", getuid() != 65534 || geteuid() != 65534);
    return 0;
}
