n, m = map(int, input().split())

def fib(k):
    # fast doubling: returns (F(k), F(k+1)) mod m
    if k == 0:
        return 0, 1 % m
    a, b = fib(k // 2)
    c = a * ((2 * b - a) % m) % m
    d = (a * a + b * b) % m
    return (d, (c + d) % m) if k % 2 else (c, d)

print(fib(n)[0] % m)
