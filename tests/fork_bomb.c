/* Expected verdict: Time Limit Exceeded.
   A fork bomb: every process keeps creating more processes. RLIMIT_NPROC makes
   fork() fail once the limit is reached, so the machine survives. The processes
   keep spinning until the judge kills the whole process group. */
#include <unistd.h>
int main(void) {
    for (;;) fork();
    return 0;
}
