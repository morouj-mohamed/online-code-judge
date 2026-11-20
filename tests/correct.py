# Expected verdict: Accepted (Python runs inside the chroot with its own runtime copy)
a, b = map(int, input().split())
print(a + b)
