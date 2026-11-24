#include <stdlib.h>
#include <string.h>
int main(void) {
    for (;;) {
        char *p = malloc(16 << 20);
        if (p) memset(p, 1, 16 << 20);
    }
}
