/* Expected verdict: Time Limit Exceeded.
   Burns CPU forever. RLIMIT_CPU delivers SIGXCPU after the CPU limit. */
int main(void) {
    volatile unsigned long x = 0;
    for (;;) x++;
    return 0;
}
