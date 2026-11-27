#define _POSIX_C_SOURCE 200809L /* sigaction under the runner's strict -std=c11 */
#include <signal.h>
#include <stdio.h>
#include <string.h>
/* Tries to write 200 MB: the 16 MB fsize limit and the 64 MB tmpfs must stop it.
   A write past RLIMIT_FSIZE fails with EFBIG and raises SIGXFSZ, whose default action kills the process before it can
   report. The handler records the signal instead, so the write fails with EFBIG and the probe reports the limit
   (sigxfsz=1 proves the kernel enforced it). It does not raise or bypass the limit. */
static volatile sig_atomic_t got_sigxfsz = 0;
static void on_sigxfsz(int sig) { (void)sig; got_sigxfsz = 1; }

int main(void) {
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_sigxfsz;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGXFSZ, &sa, NULL);
    static char block[1 << 20];
    memset(block, 'x', sizeof block);
    FILE *f = fopen("/workspace/run/fill.bin", "wb");
    if (!f) { puts("BLOCKED open"); return 0; }
    long written = 0;
    for (int i = 0; i < 200; i++) {
        size_t n = fwrite(block, 1, sizeof block, f);
        if (n != sizeof block || fflush(f) != 0) break;
        written += (long)n;
    }
    fclose(f);
    printf(written >= 200L << 20 ? "ESCAPED disk %ld sigxfsz=%d\n" : "BLOCKED disk %ld sigxfsz=%d\n", written,
           (int)got_sigxfsz);
    return 0;
}
