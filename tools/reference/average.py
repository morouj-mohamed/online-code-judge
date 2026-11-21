# Reference solution for F. Average of Numbers: Python prints as many digits as it likes.
import sys

data = sys.stdin.buffer.read().split()
n = int(data[0])
print(sum(map(int, data[1:1 + n])) / n)
