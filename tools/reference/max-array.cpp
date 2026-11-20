#include <cstdio>
int main() {
    int n;
    scanf("%d", &n);
    long long best = -4000000000000000000LL, x;
    for (int i = 0; i < n; i++) {
        scanf("%lld", &x);
        if (x > best) best = x;
    }
    printf("%lld\n", best);
    return 0;
}
