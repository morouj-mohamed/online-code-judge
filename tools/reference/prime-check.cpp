#include <iostream>
int main() {
    long long n;
    std::cin >> n;
    bool prime = n >= 2;
    for (long long d = 2; d * d <= n && prime; d++)
        if (n % d == 0) prime = false;
    std::cout << (prime ? "YES" : "NO") << "\n";
    return 0;
}
