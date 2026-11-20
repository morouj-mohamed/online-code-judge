import sys
data = sys.stdin.read().split()
n = int(data[0])
print(max(map(int, data[1:1 + n])))
