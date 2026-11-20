/* Expected verdict: Runtime Error (SIGXFSZ).
   Writes a file without end. RLIMIT_FSIZE stops it at 16 MB. */
#include <stdio.h>
#include <string.h>
static char block[1 << 20];
int main(void) {
    memset(block, 'x', sizeof block);
    FILE *f = fopen("bomb.txt", "w");
    if (!f) return 3;
    for (;;) fwrite(block, 1, sizeof block, f);
}
