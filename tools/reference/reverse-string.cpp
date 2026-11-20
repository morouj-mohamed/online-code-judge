#include <algorithm>
#include <iostream>
#include <string>
int main() {
    std::string s;
    std::getline(std::cin, s);      // getline keeps the spaces inside the line
    std::reverse(s.begin(), s.end());
    std::cout << s << "\n";
    return 0;
}
