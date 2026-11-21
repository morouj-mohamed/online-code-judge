# F. Average of Numbers

Given `n` integers, print their average (arithmetic mean).

## Input
The first line contains `n` (`1 <= n <= 10^5`). The second line contains `n` integers
`a_i` (`-10^9 <= a_i <= 10^9`).

## Output
One real number: the average. Your answer is accepted if its absolute or relative error
is at most `10^-6`, so `1.5`, `1.500000` and `1.5000000001` are all correct.

## Example
Input
```
2
1 2
```
Output
```
1.5000000000
```

## Notes
The sum of `10^5` numbers of size `10^9` does not fit in a 32-bit `int`: use `long long`.
