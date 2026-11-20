/* Expected verdict: Runtime Error (SIGFPE).
   Integer division by zero makes the CPU raise an exception. The kernel turns
   it into the signal SIGFPE. */
#include <stdio.h>
int main(int argc, char **argv) {
    (void)argv;
    volatile int zero = argc - 1;     /* 0, but the compiler cannot know it */
    int a, b;
    scanf("%d %d", &a, &b);
    printf("%d\n", (a + b) / zero);
    return 0;
}
