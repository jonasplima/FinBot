"""Chart generation service for FinBot."""

import io
from decimal import Decimal
from typing import Any

import matplotlib

matplotlib.use("Agg")  # Use non-GUI backend before importing pyplot

import matplotlib.pyplot as plt

# Color palette for charts (consistent visual identity)
CHART_COLORS = [
    "#4CAF50",  # Green
    "#2196F3",  # Blue
    "#FF9800",  # Orange
    "#9C27B0",  # Purple
    "#F44336",  # Red
    "#00BCD4",  # Cyan
    "#FFEB3B",  # Yellow
    "#795548",  # Brown
    "#607D8B",  # Blue Grey
    "#E91E63",  # Pink
]

# Chart style configuration
CHART_STYLE = {
    "figure.facecolor": "#FFFFFF",
    "axes.facecolor": "#FFFFFF",
    "axes.edgecolor": "#333333",
    "axes.labelcolor": "#333333",
    "text.color": "#333333",
    "xtick.color": "#333333",
    "ytick.color": "#333333",
    "font.size": 12,
    "axes.titlesize": 14,
    "axes.labelsize": 11,
}


class ChartService:
    """Service for generating expense charts as PNG images."""

    def __init__(self) -> None:
        """Initialize the chart service with consistent styling."""
        plt.rcParams.update(CHART_STYLE)

    def _to_float(self, value: Any) -> float:
        """Convert Decimal or other numeric types to float."""
        if isinstance(value, Decimal):
            return float(value)
        return float(value)

    def generate_pie_chart(
        self,
        data: list[dict],
        title: str = "Gastos por Categoria",
    ) -> bytes:
        """
        Generate a pie chart showing expense distribution by category.

        Args:
            data: List of dicts with 'category' and 'amount' keys
            title: Chart title

        Returns:
            PNG image as bytes
        """
        if not data:
            return self._generate_empty_chart("Sem dados para exibir")

        # Sort by amount descending
        sorted_data = sorted(data, key=lambda x: self._to_float(x["amount"]), reverse=True)

        categories = [item["category"] for item in sorted_data]
        amounts = [self._to_float(item["amount"]) for item in sorted_data]
        total = sum(amounts)

        # Create figure
        fig, ax = plt.subplots(figsize=(10, 8))

        # Create pie chart
        colors = CHART_COLORS[: len(categories)]
        wedges, texts, autotexts = ax.pie(
            amounts,
            labels=None,
            autopct=lambda pct: f"{pct:.1f}%" if pct > 5 else "",
            colors=colors,
            startangle=90,
            pctdistance=0.75,
        )

        # Style percentage text
        for autotext in autotexts:
            autotext.set_color("white")
            autotext.set_fontweight("bold")
            autotext.set_fontsize(10)

        # Add legend with values
        legend_labels = [
            f"{cat}: R$ {amt:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            for cat, amt in zip(categories, amounts, strict=True)
        ]
        ax.legend(
            wedges,
            legend_labels,
            title="Categorias",
            loc="center left",
            bbox_to_anchor=(1, 0, 0.5, 1),
            fontsize=10,
        )

        # Add title with total
        total_formatted = f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        ax.set_title(f"{title}\nTotal: {total_formatted}", fontsize=14, fontweight="bold", pad=20)

        plt.tight_layout()

        return self._figure_to_bytes(fig)

    def generate_bar_chart(
        self,
        data: list[dict],
        title: str = "Maiores Gastos do Mês",
    ) -> bytes:
        """
        Generate a horizontal bar chart showing top expenses.

        Args:
            data: List of dicts with 'description' and 'amount' keys
            title: Chart title

        Returns:
            PNG image as bytes
        """
        if not data:
            return self._generate_empty_chart("Sem dados para exibir")

        # Sort by amount descending and limit to top 10
        sorted_data = sorted(data, key=lambda x: self._to_float(x["amount"]), reverse=True)[:10]

        # Reverse for horizontal bar chart (largest at top)
        sorted_data = list(reversed(sorted_data))

        descriptions = [self._truncate_text(item["description"], 25) for item in sorted_data]
        amounts = [self._to_float(item["amount"]) for item in sorted_data]

        # Create figure
        fig, ax = plt.subplots(figsize=(10, max(6, len(descriptions) * 0.6)))

        # Create horizontal bar chart
        colors = [CHART_COLORS[i % len(CHART_COLORS)] for i in range(len(descriptions))]
        bars = ax.barh(descriptions, amounts, color=colors, edgecolor="white", linewidth=0.5)

        # Add value labels on bars
        for bar, amount in zip(bars, amounts, strict=True):
            width = bar.get_width()
            label = f"R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            ax.text(
                width + max(amounts) * 0.02,
                bar.get_y() + bar.get_height() / 2,
                label,
                ha="left",
                va="center",
                fontsize=10,
            )

        # Configure axes
        ax.set_xlabel("Valor (R$)")
        ax.set_title(title, fontsize=14, fontweight="bold", pad=20)
        ax.set_xlim(0, max(amounts) * 1.25)

        # Remove top and right spines
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        plt.tight_layout()

        return self._figure_to_bytes(fig)

    def generate_line_chart(
        self,
        data: list[dict],
        title: str = "Evolução dos Gastos",
    ) -> bytes:
        """
        Generate a line chart showing expense evolution over time.

        Args:
            data: List of dicts with 'date' (str like '01/03') and 'amount' keys
            title: Chart title

        Returns:
            PNG image as bytes
        """
        if not data:
            return self._generate_empty_chart("Sem dados para exibir")

        dates = [item["date"] for item in data]
        amounts = [self._to_float(item["amount"]) for item in data]

        # Calculate cumulative sum for trend line
        cumulative = []
        total = 0
        for amount in amounts:
            total += amount
            cumulative.append(total)

        # Create figure
        fig, ax = plt.subplots(figsize=(12, 6))

        # Plot daily amounts as bars
        ax.bar(dates, amounts, color=CHART_COLORS[0], alpha=0.6, label="Gasto diário")

        # Plot cumulative line
        ax2 = ax.twinx()
        ax2.plot(
            dates,
            cumulative,
            color=CHART_COLORS[1],
            linewidth=2,
            marker="o",
            markersize=4,
            label="Acumulado",
        )

        # Configure axes
        ax.set_xlabel("Data")
        ax.set_ylabel("Gasto Diário (R$)", color=CHART_COLORS[0])
        ax2.set_ylabel("Total Acumulado (R$)", color=CHART_COLORS[1])

        ax.tick_params(axis="y", labelcolor=CHART_COLORS[0])
        ax2.tick_params(axis="y", labelcolor=CHART_COLORS[1])

        # Rotate x-axis labels if many dates
        if len(dates) > 10:
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")

        # Add title
        total_formatted = f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        ax.set_title(f"{title}\nTotal: {total_formatted}", fontsize=14, fontweight="bold", pad=20)

        # Add legend
        lines1, labels1 = ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(lines1 + lines2, labels1 + labels2, loc="upper left")

        # Remove top spine
        ax.spines["top"].set_visible(False)
        ax2.spines["top"].set_visible(False)

        plt.tight_layout()

        return self._figure_to_bytes(fig)

    def _generate_empty_chart(self, message: str) -> bytes:
        """Generate a placeholder chart with a message when no data is available."""
        fig, ax = plt.subplots(figsize=(8, 6))
        ax.text(
            0.5,
            0.5,
            message,
            ha="center",
            va="center",
            fontsize=16,
            color="#666666",
        )
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        return self._figure_to_bytes(fig)

    def _figure_to_bytes(self, fig: plt.Figure) -> bytes:
        """Convert matplotlib figure to PNG bytes."""
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=150, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        buffer.seek(0)
        return buffer.read()

    def _truncate_text(self, text: str, max_length: int) -> str:
        """Truncate text to max_length, adding ellipsis if needed."""
        if len(text) <= max_length:
            return text
        return text[: max_length - 3] + "..."
