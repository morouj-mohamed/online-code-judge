# Expected verdict: Memory Limit Exceeded.
# bytearray(1 MB) blocks pile up until RLIMIT_AS makes Python raise MemoryError.
blocks = []
while True:
    blocks.append(bytearray(1 << 20))
