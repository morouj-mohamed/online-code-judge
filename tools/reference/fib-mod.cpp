#include <iostream>
#include <utility>
typedef unsigned long long u64;
static u64 M;
// Fast doubling: returns (F(k), F(k+1)) modulo M in O(log k) steps.
static std::pair<u64, u64> fib(u64 k) {
    if (k == 0) return {0, 1 % M};
    std::pair<u64, u64> h = fib(k / 2);
    u64 a = h.first, b = h.second;
    u64 c = a * ((2 * b + M - a) % M) % M;   // F(2j)
    u64 d = (a * a + b * b) % M;             // F(2j+1)
    if (k % 2) return {d, (c + d) % M};
    return {c, d};
}
int main() {
    u64 n;
    std::cin >> n >> M;
    std::cout << fib(n).first % M << "\n";
    return 0;
}
