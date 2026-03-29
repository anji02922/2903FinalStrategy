import os
import pandas as pd
import numpy as np
from loguru import logger
from tabulate import tabulate


class BacktestReport:
    def __init__(self, results: dict, config: dict):
        self.results = results
        self.config = config
        self.trades = results["closed_trades"]
        self.equity_curve = results["equity_curve"]
        self.initial_capital = results["initial_capital"]
        self.final_capital = results["final_capital"]
        self.report_dir = config["backtest"]["report_directory"]
        os.makedirs(self.report_dir, exist_ok=True)

    def generate(self) -> str:
        lines = []
        lines.append("=" * 60)
        lines.append("           BACKTEST REPORT")
        lines.append("=" * 60)

        if not self.trades:
            lines.append("\nNo trades executed during backtest period.")
            report = "\n".join(lines)
            logger.info(report)
            return report

        df = pd.DataFrame(self.trades)
        total_trades = len(df)
        winners = df[df["net_pnl"] > 0]
        losers = df[df["net_pnl"] <= 0]
        win_rate = len(winners) / total_trades * 100

        total_return = (self.final_capital - self.initial_capital) / self.initial_capital * 100
        gross_profit = winners["net_pnl"].sum() if len(winners) else 0
        gross_loss = abs(losers["net_pnl"].sum()) if len(losers) else 0
        total_fees = df["fees"].sum()
        net_profit = df["net_pnl"].sum()

        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        expectancy = df["net_pnl"].mean()

        # Drawdown
        eq = self.equity_curve["equity"].values if len(self.equity_curve) else np.array([self.initial_capital])
        peak = np.maximum.accumulate(eq)
        drawdown = (eq - peak) / peak * 100
        max_dd = drawdown.min()

        # Consecutive losses
        results_seq = [1 if t["net_pnl"] > 0 else 0 for t in self.trades]
        max_consec_loss = 0
        current = 0
        for r in results_seq:
            if r == 0:
                current += 1
                max_consec_loss = max(max_consec_loss, current)
            else:
                current = 0

        # Sharpe (daily returns)
        if len(self.equity_curve) > 1:
            eq_series = self.equity_curve.set_index("timestamp")["equity"]
            daily_eq = eq_series.resample("1D").last().dropna()
            daily_returns = daily_eq.pct_change().dropna()
            sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(365) if daily_returns.std() > 0 else 0
            sortino_downside = daily_returns[daily_returns < 0].std()
            sortino = daily_returns.mean() / sortino_downside * np.sqrt(365) if sortino_downside > 0 else 0
        else:
            sharpe = sortino = 0

        lines.append(f"\nPeriod: {self.config['backtest']['start_date']} to {self.config['backtest']['end_date']}")
        lines.append(f"Starting Capital: ${self.initial_capital:,.2f}")
        lines.append(f"Ending Capital:   ${self.final_capital:,.2f}")
        lines.append(f"Leverage:         {self.config['trading']['leverage']}x")

        lines.append(f"\n--- Returns ---")
        lines.append(f"Total Return:     {total_return:+.2f}%")
        lines.append(f"Net Profit:       ${net_profit:+,.2f}")

        lines.append(f"\n--- Trades ---")
        lines.append(f"Total Trades:        {total_trades}")
        lines.append(f"Win Rate:            {win_rate:.1f}%")
        lines.append(f"Avg Win:             ${winners['net_pnl'].mean():.2f}" if len(winners) else "Avg Win:             N/A")
        lines.append(f"Avg Loss:            ${losers['net_pnl'].mean():.2f}" if len(losers) else "Avg Loss:            N/A")
        lines.append(f"Largest Win:         ${winners['net_pnl'].max():.2f}" if len(winners) else "Largest Win:         N/A")
        lines.append(f"Largest Loss:        ${losers['net_pnl'].min():.2f}" if len(losers) else "Largest Loss:        N/A")
        lines.append(f"Profit Factor:       {profit_factor:.2f}")
        lines.append(f"Expectancy:          ${expectancy:.2f}/trade")

        lines.append(f"\n--- Risk ---")
        lines.append(f"Max Drawdown:        {max_dd:.2f}%")
        lines.append(f"Max Consec Losses:   {max_consec_loss}")
        lines.append(f"Sharpe Ratio:        {sharpe:.2f}")
        lines.append(f"Sortino Ratio:       {sortino:.2f}")

        # Strategy breakdown
        lines.append(f"\n--- Strategy Breakdown ---")
        for strat in df["strategy"].unique():
            s = df[df["strategy"] == strat]
            sw = s[s["net_pnl"] > 0]
            s_wr = len(sw) / len(s) * 100 if len(s) > 0 else 0
            lines.append(f"  {strat}: {len(s)} trades, {s_wr:.1f}% WR, ${s['net_pnl'].sum():+.2f} PnL")

        # Exit reasons
        lines.append(f"\n--- Exit Reasons ---")
        for reason in df["exit_reason"].unique():
            r = df[df["exit_reason"] == reason]
            lines.append(f"  {reason}: {len(r)} trades, ${r['net_pnl'].sum():+.2f}")

        lines.append(f"\n--- Fees ---")
        lines.append(f"Total Fees Paid:     ${total_fees:,.2f}")
        lines.append(f"Gross Profit:        ${df['raw_pnl'].sum():+,.2f}")
        lines.append(f"Net Profit:          ${net_profit:+,.2f}")

        lines.append("=" * 60)

        report = "\n".join(lines)
        logger.info("\n" + report)

        # Save report
        with open(os.path.join(self.report_dir, "backtest_report.txt"), "w") as f:
            f.write(report)

        # Save trades CSV
        df.to_csv(os.path.join(self.report_dir, "trades.csv"), index=False)

        # Generate equity chart
        self._plot_equity()

        return report

    def _plot_equity(self):
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            if len(self.equity_curve) == 0:
                return

            fig, axes = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [3, 1]})

            # Equity curve
            ax1 = axes[0]
            ax1.plot(self.equity_curve["timestamp"], self.equity_curve["equity"], linewidth=1, color="blue")
            ax1.axhline(y=self.initial_capital, color="gray", linestyle="--", alpha=0.5)
            ax1.set_title("Equity Curve")
            ax1.set_ylabel("Capital ($)")
            ax1.grid(True, alpha=0.3)

            # Drawdown
            eq = self.equity_curve["equity"].values
            peak = np.maximum.accumulate(eq)
            dd = (eq - peak) / peak * 100
            ax2 = axes[1]
            ax2.fill_between(self.equity_curve["timestamp"], dd, 0, alpha=0.4, color="red")
            ax2.set_title("Drawdown (%)")
            ax2.set_ylabel("DD %")
            ax2.grid(True, alpha=0.3)

            plt.tight_layout()
            plt.savefig(os.path.join(self.report_dir, "equity_curve.png"), dpi=150)
            plt.close()
            logger.info("Equity chart saved")
        except Exception as e:
            logger.warning(f"Could not generate chart: {e}")
