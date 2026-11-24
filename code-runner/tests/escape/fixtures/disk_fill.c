#include <stdio.h>
#include <string.h>
/* Tries to write 200 MB: the 16 MB fsize limit and the 64 MB tmpfs must stop it. */
int main(void) {
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
    printf(written >= 200L << 20 ? "ESCAPED disk %ld\n" : "BLOCKED disk %ld\n", written);
    return 0;
}
