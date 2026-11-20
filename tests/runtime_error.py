# Expected verdict: Runtime Error (uncaught exception, exit code 1)
a, b = map(int, input().split())
print(a // (a - a))
