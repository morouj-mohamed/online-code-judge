// Expected verdict: Accepted (about 350 ms of CPU per test, limit is 1000 ms).
// Shows that a slow program is fine as long as it stays under the limit.
#include <cstdio>
#include <ctime>
int main() {
    long long a, b;
    scanf("%lld %lld", &a, &b);
    clock_t start = clock();                         // CPU time of this process
    volatile unsigned long long x = 0;
    while ((double)(clock() - start) / CLOCKS_PER_SEC < 0.35) x++;
    printf("%lld\n", a + b);
    return 0;
}
