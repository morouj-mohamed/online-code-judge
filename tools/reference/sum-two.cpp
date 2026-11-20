#include <iostream>
int main() {
    long long a, b;                 // 64-bit: the sum of two 10^18 values needs it
    std::cin >> a >> b;
    std::cout << a + b << "\n";
    return 0;
}
