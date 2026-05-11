"""Verify the corrected Kelly FOC quadratic against grid-search optimum.

3-outcome Kelly: outcomes (win, partial, loss) with probabilities (p, r_o, q),
payoffs (+b, +alpha*b, -1) per dollar wagered.

G(w) = p log(1+wb) + r_o log(1+w*alpha*b) + q log(1-w)
FOC: pb/(1+wb) + r_o*alpha*b/(1+w*alpha*b) - q/(1-w) = 0

Clearing denominators with (1+wb)(1+w*alpha*b)(1-w) gives a quadratic A w^2 + B w + C = 0.
"""
import math
import numpy as np

def G(w, p, ro, q, alpha, b):
    if w >= 1.0 or w <= -1.0/(alpha*b) or w <= -1.0/b:
        return -1e9
    if 1 - w <= 0 or 1 + w*b <= 0 or 1 + w*alpha*b <= 0:
        return -1e9
    return p*math.log(1+w*b) + ro*math.log(1+w*alpha*b) + q*math.log(1-w)

def grid_optimum(p, ro, q, alpha, b):
    grid = np.linspace(0.0001, 0.9999, 10000)
    vals = np.array([G(w, p, ro, q, alpha, b) for w in grid])
    return grid[np.argmax(vals)]

def doc_quadratic(p, ro, q, alpha, b):
    """The buggy quadratic from ground_additions.md."""
    A = -alpha * b**2
    B = b * (p*(1-alpha) + alpha*(p+ro) - q*(1+alpha))
    C = p*b + ro*alpha*b - q
    return A, B, C

def corrected_quadratic(p, ro, q, alpha, b):
    """Correctly derived from FOC by clearing (1+wb)(1+w*alpha*b)(1-w)."""
    A = -alpha * b**2
    B = alpha * b**2 * (p + ro) - b * (p + ro*alpha + q*(1+alpha))
    C = p*b + ro*alpha*b - q
    return A, B, C

def solve(A, B, C):
    """Pick the root in (0, 1)."""
    disc = B*B - 4*A*C
    if disc < 0:
        return None, None
    s = math.sqrt(disc)
    r1 = (-B + s) / (2*A)
    r2 = (-B - s) / (2*A)
    return r1, r2

def in_unit(x):
    return x is not None and 0 < x < 1

cases = [
    (0.4, 0.3, 0.3, 0.5, 1.5),
    (0.5, 0.2, 0.3, 0.4, 2.0),
    (0.6, 0.1, 0.3, 0.6, 1.0),
    (0.7, 0.15, 0.15, 0.3, 0.8),
    (0.45, 0.4, 0.15, 0.5, 1.2),
]

print(f"{'p':>5}{'ro':>6}{'q':>6}{'alpha':>7}{'b':>5}    {'grid':>8}    {'doc':>8}    {'corr':>8}")
for p, ro, q, alpha, b in cases:
    w_grid = grid_optimum(p, ro, q, alpha, b)
    A_d, B_d, C_d = doc_quadratic(p, ro, q, alpha, b)
    A_c, B_c, C_c = corrected_quadratic(p, ro, q, alpha, b)
    rd = [r for r in solve(A_d, B_d, C_d) if in_unit(r)]
    rc = [r for r in solve(A_c, B_c, C_c) if in_unit(r)]
    w_doc = rd[0] if rd else float("nan")
    w_corr = rc[0] if rc else float("nan")
    print(f"{p:5.2f}{ro:6.2f}{q:6.2f}{alpha:7.2f}{b:5.2f}    {w_grid:8.4f}    {w_doc:8.4f}    {w_corr:8.4f}")

# Boundary check: alpha = -1 collapses to binary Kelly w* = (pb - q)/b
print("\nBinary collapse (alpha=-1, ro=0):")
p, ro, q, alpha, b = 0.55, 0.0, 0.45, -1.0, 2.0
expected = (p*b - q)/b
A_c, B_c, C_c = corrected_quadratic(p, ro, q, alpha, b)
roots = solve(A_c, B_c, C_c)
print(f"  expected w* = {expected:.4f}")
print(f"  corrected roots = {roots}")
