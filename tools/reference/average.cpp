// Reference solution for F. Average of Numbers: sum in 64 bits, print 6 decimals.
#include <cstdio>
int main() {
    int n;
    if (scanf("%d", &n) != 1) return 0;
    long long sum = 0;
    for (int i = 0; i < n; i++) { long long x; scanf("%lld", &x); sum += x; }
    printf("%.6f\n", (double)sum / n);
}
