import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
import matplotlib.dates as mdates
import numpy as np


def create_stock_price_grid(pandas_df, top_25_stocks, ncols=3):
    """
    Create a grid of stock price plots with max_close, min_close, and close values.

    Parameters:
    -----------
    pandas_df : pd.DataFrame
        DataFrame containing stock data with columns: Date, symbol, Close, volatility_score, max_close, min_close.
    top_25_stocks : list
        List of stock symbols to plot.
    ncols : int, optional
        Number of columns in the grid (default: 3).

    Returns:
    --------
    fig : matplotlib.figure.Figure
        The created figure object.
    """
    # Convert Date column to datetime format
    pandas_df["Date"] = pd.to_datetime(pandas_df["Date"], errors='coerce')

    # Filter and sort stocks by volatility_score (descending)
    sorted_df = pandas_df[pandas_df["symbol"].isin(top_25_stocks)].copy()
    sorted_df = sorted_df.sort_values(by="volatility_score", ascending=False)

    # Filter and pre-sort stocks
    # filtered_stocks = {
    #    symbol: pandas_df[pandas_df["symbol"] == symbol].sort_values("Date")
    #    for symbol in top_25_stocks
    # }

    # Create a dictionary of sorted stock data
    filtered_stocks = {
        symbol: sorted_df[sorted_df["symbol"] == symbol].sort_values("Date")
        for symbol in sorted_df["symbol"].unique()
    }

    # Calculate layout
    nrows = int(np.ceil(len(top_25_stocks) / ncols))

    # Create figure and axes
    fig, axes = plt.subplots(
        nrows=nrows,
        ncols=ncols,
        figsize=(18, 5 * nrows),
        sharex=False,  # Disable shared x-axis so each plot gets its own label
        sharey=False  # Disable shared y-axis
    )
    axes = axes.flatten()

    # Set style
    plt.style.use('seaborn-v0_8')

    # Iterate over stocks and create plots
    for i, (symbol, stock_data) in enumerate(filtered_stocks.items()):
        ax = axes[i]

        if stock_data.empty:
            _handle_empty_plot(ax, symbol)
            continue

        _create_stock_plot(ax, stock_data, symbol)

    # Clean up unused axes
    for j in range(i + 1, len(axes)):
        axes[j].axis('off')

    # Apply tight layout to ensure labels don't overlap
    plt.tight_layout()

    return fig


def _handle_empty_plot(ax, symbol):
    """Handle cases where there's no data for a symbol."""
    ax.text(
        0.5, 0.5, f"No Data for {symbol}",
        horizontalalignment='center',
        verticalalignment='center',
        transform=ax.transAxes,
        fontsize=12,
        color='red'
    )
    ax.set_xticks([])
    ax.set_yticks([])


def _create_stock_plot(ax, stock_data, symbol):
    """Create individual stock plot on given axis."""
    # Ensure numeric conversion
    stock_data["Close"] = pd.to_numeric(stock_data["Close"], errors='coerce')
    stock_data["max_close"] = pd.to_numeric(stock_data["max_close"], errors='coerce')
    stock_data["min_close"] = pd.to_numeric(stock_data["min_close"], errors='coerce')

    # Get values for the title
    max_close = stock_data["max_close"].iloc[0]
    min_close = stock_data["min_close"].iloc[0]
    volatility_score = stock_data["volatility_score"].iloc[0]
    earnings_date = stock_data["earnings_date"].iloc[0]

    # Plot Close as a line graph
    ax.plot(
        stock_data["Date"],
        stock_data["Close"],
        marker='o',
        linestyle='-',
        label="Close Price",
        linewidth=2,
        color="blue"
    )

    # Annotate each Close value on the graph
    for date, close in zip(stock_data["Date"], stock_data["Close"]):
        ax.text(
            date, close, f"{close:.2f}",
            fontsize=10,
            ha="right",
            va="bottom",
            color="black",
            bbox=dict(facecolor='white', edgecolor='black', boxstyle='round,pad=0.3')
        )

    # Set title with max_close, min_close, and volatility score
    ax.set_title(
        f"{symbol} | Max: {max_close:.2f}, Min: {min_close:.2f}, Volatility: {volatility_score:.2f}, ERD: {earnings_date}",
        fontsize=12,
        pad=10
    )

    # Set individual x-axis label for each chart
    ax.set_xlabel("Date", fontsize=10)

    # Set y-axis label
    ax.set_ylabel("Close Price", fontsize=10)

    ax.grid(True, alpha=0.3)
    ax.legend(loc='upper left')

    # Configure x-axis for proper date formatting
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))

    # Ensure date labels are readable
    ax.tick_params(axis='x', rotation=45)

