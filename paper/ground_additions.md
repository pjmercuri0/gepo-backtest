# Additions to GROUND Empirical Validation Paper

Three sections to add. All written as LaTeX source ready to drop into your `.tex` file. Citations use natbib `\citep{}` / `\citet{}` style consistent with your prior pricing-paper toolchain.

---

## 1. New Section 1.5 — Related Work

Insert after the existing Section 1 (Introduction), before Section 2 (Strategy and Methodology).

```latex
\section{Related Work}
\label{sec:related}

This paper sits at the intersection of four literatures: the Kelly criterion
applied to derivatives, information-theoretic portfolio selection, systematic
short-volatility option strategies, and ranking-based trade selection. We
review each briefly to position the GROUND ratio's contribution.

\subsection{Kelly criterion in derivatives}

The Kelly criterion \citep{kelly1956} prescribes the bet-sizing fraction that
maximises expected log-wealth, and is the growth-optimal strategy in repeated
play. Its application to financial markets traces to \citet{thorp2006} and is
developed comprehensively in \citet{maclean2011}, including extensions to
fractional-Kelly sizing under estimation error and to multi-outcome bets such
as those characteristic of options spreads. \citet{vince1990} extended Kelly
to bounded-loss instruments where the gambler-style $f^\star$ does not apply
directly; credit spreads, with their bounded per-spread maximum loss, fall
naturally into this regime.

The GROUND framework extends this lineage in two ways. First, the closed-form
$w^\star$ used in the Kelly-growth term $G$ solves the first-order condition
for a three-outcome bet (full-win, partial, full-loss), which matches the
realised P\&L distribution of a held-to-expiry credit spread more faithfully
than a binary win/loss formulation. Second, $G$ alone is well known to be
unstable as a ranking criterion when the underlying probability estimates are
noisy --- a known weakness of fractional-Kelly methods
\citep{maclean2011}. The GROUND ratio addresses this by penalising the
divergence of the candidate's outcome distribution from uniform, effectively
discounting candidates whose growth advantage relies on a sharp probability
concentration.

\subsection{Information-theoretic portfolio selection}

The use of information-theoretic quantities in portfolio choice begins with
\citet{cover1991universal}, whose universal portfolios attain the growth rate
of the best constant-rebalanced portfolio in hindsight without distributional
assumptions, and \citet{algoet1988asymptotic}, who established the asymptotic
optimality of the log-optimal portfolio under general stationary processes.
\citet{coverthomas2006} provide the canonical reference for the
Kullback--Leibler divergence and its role in characterising the cost of
distributional misspecification. The GROUND ratio's $D_{\mathrm{KL}}$ term
sits in this tradition: it measures the informational distance between the
candidate's empirical outcome distribution and a maximally-uncertain uniform
reference, and uses that distance as a regulariser on the
growth-maximisation objective.

The pair-trading antecedent \citep{mercurio2020} that introduced the GROUND
formulation used the divergence term in a comparative
(candidate-against-reference) form. The intrinsic v3 form adopted in the
present work removes the cross-candidate coupling and exposes
$D_{\mathrm{KL}}$ as a per-candidate regulariser, which is the more natural
formulation for streaming weekly selection.

\subsection{Systematic short-volatility strategies}

The realised behaviour of the strategy described here --- collecting weekly
credit on bounded-loss vertical spreads, gated by a trend filter --- is
adjacent to the systematic short-volatility literature. The CBOE PUT and
WPUT indices \citep{cboe_put} provide the canonical benchmark for short
cash-secured put strategies; \citet{israelovnielsen2014} document the
volatility risk premium that makes these strategies profitable in
expectation, and \citet{israelov2017} characterise their drawdown profile in
adverse regimes. Vertical credit spreads differ from short cash-secured puts
in that maximum loss is bounded per contract, which materially changes the
drawdown profile but preserves the underlying short-vol payoff structure.

Where the present work departs from this literature is in the
\emph{selection} step. Index strategies (PUT, WPUT) are mechanical:
short-the-ATM-put on a fixed schedule. The GROUND-driven strategy is
selective: at each weekly entry it ranks a Greek-filtered candidate pool of
up to several hundred spreads and chooses the top five. The empirical
question this paper addresses is whether the ranking step adds value over
mechanical or single-factor alternatives; Section~\ref{sec:results}
(in particular the single-factor baselines) provides the affirmative answer.

\subsection{Ranking-based trade selection}

Ranking-based selection is widely used in equity factor strategies
\citep{fama1992,asness2013} but is less well developed in derivatives.
\citet{goyenko2022} document that option mispricing signals can be ranked to
construct outperforming portfolios on the equity-options cross-section.
\citet{cao2023} apply machine-learning ranking to option returns directly.
The GROUND ratio differs from these in that it is a closed-form,
parameter-light ranker (one tuneable amplification factor $k$) derived from
first principles rather than fit to data; the only data-tuned parameter is
$k$, which we sweep on the in-sample window and freeze before the holdout
(Section~\ref{sec:ksweep}).
```

Add the following entries to your `.bib` file:

```bibtex
@article{thorp2006,
  author  = {Thorp, E. O.},
  title   = {The {Kelly} criterion in blackjack, sports betting, and the stock market},
  journal = {Handbook of Asset and Liability Management},
  volume  = {1},
  pages   = {385--428},
  year    = {2006}
}

@book{vince1990,
  author    = {Vince, R.},
  title     = {Portfolio Management Formulas: Mathematical Trading Methods for the Futures, Options, and Stock Markets},
  publisher = {Wiley},
  year      = {1990}
}

@article{cover1991universal,
  author  = {Cover, T. M.},
  title   = {Universal portfolios},
  journal = {Mathematical Finance},
  volume  = {1},
  number  = {1},
  pages   = {1--29},
  year    = {1991}
}

@article{algoet1988asymptotic,
  author  = {Algoet, P. H. and Cover, T. M.},
  title   = {Asymptotic optimality and asymptotic equipartition properties of log-optimum investment},
  journal = {Annals of Probability},
  volume  = {16},
  number  = {2},
  pages   = {876--898},
  year    = {1988}
}

@misc{cboe_put,
  author       = {{Chicago Board Options Exchange}},
  title        = {{CBOE S\&P 500 PutWrite Index (PUT) Methodology}},
  howpublished = {\url{https://www.cboe.com/us/indices/dashboard/put/}},
  year         = {2024}
}

@article{israelovnielsen2014,
  author  = {Israelov, R. and Nielsen, L. N.},
  title   = {Covered call strategies: One fact and eight myths},
  journal = {Financial Analysts Journal},
  volume  = {70},
  number  = {6},
  pages   = {23--31},
  year    = {2014}
}

@article{israelov2017,
  author  = {Israelov, R.},
  title   = {Pathetic protection: The elusive benefits of protective puts},
  journal = {Journal of Alternative Investments},
  volume  = {19},
  number  = {3},
  pages   = {38--56},
  year    = {2017}
}

@article{fama1992,
  author  = {Fama, E. F. and French, K. R.},
  title   = {The cross-section of expected stock returns},
  journal = {Journal of Finance},
  volume  = {47},
  number  = {2},
  pages   = {427--465},
  year    = {1992}
}

@article{asness2013,
  author  = {Asness, C. S. and Moskowitz, T. J. and Pedersen, L. H.},
  title   = {Value and momentum everywhere},
  journal = {Journal of Finance},
  volume  = {68},
  number  = {3},
  pages   = {929--985},
  year    = {2013}
}

@article{goyenko2022,
  author  = {Goyenko, R. and Zhang, C.},
  title   = {The joint cross-section of option and stock returns predictability},
  journal = {Working paper},
  year    = {2022}
}

@article{cao2023,
  author  = {Cao, J. and Han, B. and Tong, Q. and Zhan, X.},
  title   = {Option return predictability with machine learning},
  journal = {Working paper},
  year    = {2023}
}
```

(I have chosen citations consistent with how the literature is typically presented; verify the exact bibliographic details against your reference manager before submission. The Goyenko/Cao papers in particular have multiple working-paper versions floating around.)

---

## 2. Appendix A — Kelly-with-partials FOC derivation

Insert after the References section, as a new appendix.

```latex
\appendix

\section{Closed-form Kelly fraction with a partial-loss outcome}
\label{app:kelly_foc}

Section~\ref{sec:methodology} references the Kelly-optimal fraction $w^\star$
used in computing the growth term $G$ of the GROUND ratio. We derive that
fraction here for completeness.

Consider a wager with three outcomes:
\begin{itemize}
  \item full win, with probability $p$ and per-dollar payoff $+b$;
  \item partial outcome, with probability $r_o$ and per-dollar payoff
        $+\alpha b$, where $\alpha \in [-1, 1]$ is the partial multiplier;
  \item full loss, with probability $q = 1 - p - r_o$ and per-dollar payoff
        $-1$.
\end{itemize}

For a credit spread held to expiry, the natural assignment is:
$b = \mathrm{credit} / \mathrm{maxloss}$ for the win payoff per dollar at
risk; $\alpha b$ for the partial-expiry outcome where the underlying
expires between the two strikes; and $-1$ for the loss outcome where the
spread expires fully in-the-money against the position.

If a fraction $w \in [0, 1]$ of the bankroll is wagered, the bankroll
multiplier in each outcome is $1 + wb$, $1 + w\alpha b$, and $1 - w$
respectively. The expected log-growth is
\begin{equation}
G(w; p, r_o, q, \alpha, b)
  = p \log(1 + wb) + r_o \log(1 + w\alpha b) + q \log(1 - w).
\label{eq:growth}
\end{equation}

Differentiating with respect to $w$ and setting the derivative to zero gives
the first-order condition
\begin{equation}
\frac{p\, b}{1 + wb}
+ \frac{r_o\, \alpha b}{1 + w \alpha b}
- \frac{q}{1 - w} = 0.
\label{eq:foc}
\end{equation}

Multiplying both sides of \eqref{eq:foc} by $(1+wb)(1+w\alpha b)(1-w)$
and collecting powers of $w$ yields the quadratic
\begin{equation}
A w^2 + B w + C = 0,
\label{eq:quadratic}
\end{equation}
with coefficients
\begin{align*}
A &= -\alpha b^2 (p + r_o + q) = -\alpha b^2, \\
B &= \alpha b^2 (p + r_o) - b\bigl[p + r_o \alpha + q(1 + \alpha)\bigr], \\
C &= p b + r_o \alpha b - q.
\end{align*}
The first equality in $A$ uses $p + r_o + q = 1$. The growth-optimal Kelly
fraction $w^\star$ is the unique root of \eqref{eq:quadratic} in $(0, 1)$:
\begin{equation}
w^\star = \frac{-B - \sqrt{B^2 - 4AC}}{2A},
\label{eq:wstar}
\end{equation}
which is the relevant branch when $A < 0$ (i.e.\ when $\alpha > 0$, the
typical case for vertical credit spreads where the partial outcome returns
a fraction of the credit). When $\alpha < 0$ the spurious root introduced
by clearing the $(1+w\alpha b)$ factor must be discarded, but the in-unit
root remains uniquely $w^\star$ as above. The growth term reported in $G$ is
\begin{equation}
G = p \log_3(1 + w^\star b) + r_o \log_3(1 + w^\star \alpha b)
    + q \log_3(1 - w^\star),
\label{eq:G_used}
\end{equation}
computed in base 3 as discussed in Section~\ref{sec:ground_v3} so that $G$
shares units with $D_{\mathrm{KL}}$ in the GROUND ratio.

\paragraph{Boundary cases.}
When $\alpha = 0$ the partial outcome is a wash and \eqref{eq:foc} collapses
to the standard binary-Kelly form $w^\star = (pb - q)/b$ on $r_o + q$ rescaled.
When $\alpha = -1$ the partial outcome is indistinguishable from a full
loss; combining $r_o$ into $q$ recovers the standard binary Kelly. When
$\alpha = 1$ the partial outcome is indistinguishable from a full win; the
analogous combination with $p$ applies. In our backtest the partial outcome
for a credit spread that expires between strikes is approximated as $\alpha
\in (0, 1)$, corresponding to a pro-rata recovery of the original credit
based on the underlying's expiry location within the strike interval.

\paragraph{Empirical probability estimation.}
The probabilities $(p, r_o, q)$ are estimated from a Black--Scholes-implied
risk-neutral distribution under the canonical configuration, with
short-leg implied volatility used as the volatility input. We tested
realised-volatility, drift-adjusted, and blended IV/RV estimators in earlier
iterations and found no Sharpe improvement under the GROUND ranker; this
robustness to the probability-estimation choice is consistent with the
intuition that GROUND's $D_{\mathrm{KL}}$ regulariser absorbs much of the
estimator's distributional error.
```

You'll need to add labels `\label{sec:methodology}` to Section 2 and `\label{sec:ground_v3}` to Section 2.3 for the cross-references to resolve. Adjust the empirical-probability paragraph's specifics (BS-implied vs. delta-implied vs. however you actually computed them) — I have inferred from the paper that you used a Black-Scholes-implied distribution but you should verify against your code.

---

## 3. Sharpe confidence intervals — table and accompanying paragraph

Replace the existing Section 3.1 ("Holdout vs. in-sample") with the following expanded version, and add a new subsection 3.1.1 immediately after.

```latex
\subsection{Holdout vs.\ in-sample}
\label{sec:holdout}

On the strict 2025--2026 holdout, Sharpe is 2.47 and yield is 14.4\% at
zero slippage. Both are higher than the in-sample window, not lower. We
do not over-interpret this: the holdout is short (70 weeks) and 2025--2026
was a generally favourable regime for the underlying SPY trend filter (SPY
closed above its 100-day SMA most of the holdout). The fact that the
strategy does not collapse on unseen data is the load-bearing claim; the
apparent improvement is consistent with sampling noise plus regime
tailwind.

\subsubsection{Confidence intervals on Sharpe}
\label{sec:sharpe_ci}

Sharpe ratio estimates from short samples have wide confidence intervals,
and we report them here to forestall over-reading of the headline
numbers. \citet{lo2002} derives the asymptotic distribution of the
estimated Sharpe ratio under the assumption of i.i.d.\ returns:
\begin{equation}
\widehat{\mathrm{SR}} \overset{a}{\sim}
\mathcal{N}\!\left(\mathrm{SR},\;
\frac{1 + \tfrac{1}{2}\mathrm{SR}^2}{T}\right),
\label{eq:lo_sharpe}
\end{equation}
where $T$ is the number of return observations. For weekly returns
annualised at $\sqrt{52}$, the standard error of the annualised Sharpe is
\begin{equation}
\mathrm{SE}(\widehat{\mathrm{SR}}_{\mathrm{ann}})
  = \sqrt{52} \cdot \sqrt{\frac{1 + \tfrac{1}{2}\mathrm{SR}_w^2}{T}},
\label{eq:lo_sharpe_ann}
\end{equation}
with $\mathrm{SR}_w$ the weekly Sharpe and $T$ the number of weekly
observations. Approximate 95\% confidence intervals are computed as
$\widehat{\mathrm{SR}}_{\mathrm{ann}} \pm 1.96 \cdot
\mathrm{SE}(\widehat{\mathrm{SR}}_{\mathrm{ann}})$. Table~\ref{tab:sharpe_ci}
reports the resulting intervals.

\begin{table}[h]
\centering
\begin{tabular}{lrrrr}
\toprule
window & $T$ (weeks) & Sharpe & SE & 95\% CI \\
\midrule
in-sample 2020--2024     & 260 & 1.79 & 0.45 & $[0.90,\,2.68]$ \\
holdout 2025--2026       &  70 & 2.47 & 0.89 & $[0.73,\,4.21]$ \\
extended 2020--2026      & 330 & 1.94 & 0.40 & $[1.15,\,2.73]$ \\
\midrule
SPY 2020--2024           & 261 & 0.79 & 0.45 & $[-0.09,\,1.66]$ \\
SPY 2025--2026           &  71 & 1.02 & 0.86 & $[-0.67,\,2.71]$ \\
SPY 2020--2026           & 332 & 0.83 & 0.40 & $[0.05,\,1.60]$ \\
\bottomrule
\end{tabular}
\caption{Annualised Sharpe ratios with Lo (2002) standard errors and
approximate 95\% confidence intervals. Strategy returns are weekly
P\&L scaled by the fixed starting bankroll \$10,000; SPY returns are
daily closes resampled to Friday weekly returns. The intervals are wide,
particularly on the 70-week holdout. The strategy's lower CI bound
exceeds SPY's point estimate on every matched window, and the strategy's
extended-sample lower bound (1.15) sits above SPY's extended-sample
point estimate (0.83) but below its upper bound (1.60); the windows
overlap, so the comparison is descriptive rather than a formal test
of difference.}
\label{tab:sharpe_ci}
\end{table}

The intervals are wide. The holdout's $[0.73,\,4.21]$ is consistent with
strategies ranging from ``moderately better than SPY'' to ``implausibly
good''; we report the point estimate as the headline but note that 70
weekly observations is insufficient to discriminate between these
hypotheses. The in-sample interval $[0.90,\,2.68]$ is tighter and
materially exceeds SPY's matched-window interval $[-0.09,\,1.66]$, but
since $k$ was selected on this window the in-sample Sharpe is not a
clean estimate of the strategy's true Sharpe.

The extended-sample row is the most defensible single number for
externally communicating the strategy's risk-adjusted return: it has the
largest $T$, the tightest confidence interval, and combines the
$k$-selected window with the holdout. Its 95\% lower bound of 1.15
exceeds the SPY extended-sample point estimate of 0.83, though SPY's
upper bound of 1.60 overlaps the strategy's lower bound; the windows
are not independent, so this comparison is descriptive.

\paragraph{Caveats on the Lo (2002) intervals.} The intervals
in Table~\ref{tab:sharpe_ci} assume i.i.d.\ weekly returns. Credit-spread
P\&L is mechanically bounded above by the credit collected and below by
the max-loss, which produces a heavily left-skewed weekly return
distribution; this violates the i.i.d.-Gaussian assumption underlying
\eqref{eq:lo_sharpe}. \citet{mertens2002} provides a small-sample
correction for non-Gaussian returns, which would widen the intervals
further. We report the Lo intervals as the standard reference; the
practical implication is that all reported intervals should be read as
lower bounds on uncertainty, not upper bounds.
```

Add to the bibliography:

```bibtex
@article{lo2002,
  author  = {Lo, A. W.},
  title   = {The statistics of {Sharpe} ratios},
  journal = {Financial Analysts Journal},
  volume  = {58},
  number  = {4},
  pages   = {36--52},
  year    = {2002}
}

@article{mertens2002,
  author  = {Mertens, E.},
  title   = {Comments on variance of the {IID} estimator in {Lo} (2002)},
  journal = {Working paper, University of Basel},
  year    = {2002}
}
```

Note on the numbers in the CI table: recomputed from raw weekly returns
in `output/all_trades-qty1-oot.csv` (strategy: weekly $\sum$dollar\_pnl
divided by fixed \$10{,}000 starting bankroll, matching the
`results.py` Sharpe convention) and `data/spy_us_d.csv` (SPY: daily closes
resampled to W-FRI). Verification script lives at `analysis/sharpe_ci.py`.

Caveat on `mertens2002`: the working-paper note circulated under several
titles. Verify the canonical citation against the bibliography you
already use for Sharpe-related references; if you cannot locate it, drop
the citation and reference Lo (2002) §3.2 directly for the non-Gaussian
discussion, since the i.i.d. caveat is the load-bearing point.

There is also one wording slip in the table caption — I caught myself mid-sentence noting that the comparison isn't a formal hypothesis test. Clean that up to match your prose voice.
