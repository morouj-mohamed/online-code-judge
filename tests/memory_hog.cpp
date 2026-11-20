// Expected verdict: Memory Limit Exceeded.
// Allocates and touches 1 MB blocks forever. When RLIMIT_AS is reached,
// operator new throws std::bad_alloc, and the uncaught exception aborts the program.
#include <cstring>
#include <vector>
int main() {
    std::vector<char*> blocks;
    for (;;) {
        char* p = new char[1 << 20];
        std::memset(p, 1, 1 << 20);
        blocks.push_back(p);
    }
}
