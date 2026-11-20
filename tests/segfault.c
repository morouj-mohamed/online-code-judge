/* Expected verdict: Runtime Error (SIGSEGV).
   Writes to address 0, which is never mapped. */
int main(void) {
    volatile int *p = (volatile int *)0;
    *p = 1;
    return 0;
}
