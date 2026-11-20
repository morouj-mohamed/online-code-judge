// Expected verdict: Wrong Answer, on test 5.
// Classic bug: a 32-bit int overflows when 10^9 + 10^9 is added.
// Tests 1-4 are small, so they pass; test 5 is the first that fails.
#include <cstdio>
int main() {
    int a, b;                 // should be long long
    scanf("%d %d", &a, &b);
    printf("%d\n", (int)((unsigned)a + (unsigned)b));
    return 0;
}
