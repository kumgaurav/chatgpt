import matplotlib.pyplot as plt
import numpy as np

def create_diverging_prediction_chart_old(df):
    # Convert Spark DataFrame to Pandas
    pandas_df = df.copy()
    # Calculate differences
    pandas_df['low_change'] = pandas_df['Low'] - pandas_df['pred_Low']
    pandas_df['high_change'] = pandas_df['High'] - pandas_df['pred_High']
    pandas_df['close_change'] = pandas_df['Close'] - pandas_df['Predicted_Price']

    # Assign colors based on conditions
    def get_color(change):
        return 'red' if change < 0 else 'green'

    pandas_df['low_color'] = pandas_df['low_change'].apply(get_color)
    pandas_df['high_color'] = pandas_df['high_change'].apply(get_color)
    pandas_df['close_color'] = pandas_df['close_change'].apply(get_color)

    # Sort by Symbol and Date for proper ordering
    pandas_df.sort_values(['symbol', 'Date'], inplace=True)

    # Define metrics and titles
    metrics = ['low_change', 'high_change', 'close_change']
    titles = ['Low Price Predictions', 'High Price Predictions', 'Close Price Predictions']
    actual_columns = ['Low', 'High', 'Close']
    predicted_columns = ['pred_Low', 'pred_High', 'Predicted_Price']
    # Create plots: One row per symbol, 3 columns for Low, High, Close
    for symbol in pandas_df['symbol'].unique():
        symbol_df = pandas_df[pandas_df['symbol'] == symbol]

        fig, axes = plt.subplots(1, 3, figsize=(18, 5), dpi=80, sharey=True)

        for i, ax in enumerate(axes):
            metric = metrics[i]
            actual_col = actual_columns[i]
            pred_col = predicted_columns[i]

            # Horizontal lines at change values
            ax.hlines(y=symbol_df['Date'], xmin=0, xmax=symbol_df[metric], colors='black', linewidth=2)

            # Add text labels with format: "change (Symbol, Actual, Predicted)"
            for x, y, actual, pred in zip(symbol_df[metric], symbol_df['Date'], symbol_df[actual_col],
                                          symbol_df[pred_col]):
                label = f"{x:.2f} ({symbol}, {actual:.2f}, {pred:.2f})"
                ax.text(x, y, label, ha='right' if x < 0 else 'left',
                        va='center', fontdict={'color': 'red' if x < 0 else 'green', 'size': 10})

            # Add "change (Symbol, Actual, Predicted)" in the top-left corner
            subtitle_text = f"Change ({symbol}, {actual_col}, {pred_col})"
            ax.text(0.05, 0.95, subtitle_text, transform=ax.transAxes, fontsize=12,
                    verticalalignment='top', horizontalalignment='left', bbox=dict(facecolor='white', alpha=0.6))

            max_idx = symbol_df[metric].idxmax()
            min_idx = symbol_df[metric].idxmin()
            ax.scatter(symbol_df.loc[max_idx, metric], symbol_df.loc[max_idx, 'Date'], color='blue', s=100,
                       label='Max Change')
            ax.scatter(symbol_df.loc[min_idx, metric], symbol_df.loc[min_idx, 'Date'], color='purple', s=100,
                       label='Min Change')

            # Formatting
            ax.set_title(f"{titles[i]} ({symbol})", fontsize=14)
            ax.grid(linestyle='--', alpha=0.5)
            ax.set_xlim(symbol_df[metric].min() - 0.5, symbol_df[metric].max() + 0.5)
            ax.set_xlabel('Change Value')

            # Set y-axis labels as dates
            ax.set_yticks(symbol_df['Date'])
            ax.set_yticklabels(symbol_df['Date'].astype(str), fontsize=10)

        # Adjust layout and show the row of plots
        plt.tight_layout()
        plt.show()



def create_diverging_prediction_chart(df):
    pandas_df = df.copy()

    # Calculate differences
    pandas_df['low_change'] = pandas_df['Low'] - pandas_df['pred_Low']
    pandas_df['high_change'] = pandas_df['High'] - pandas_df['pred_High']
    pandas_df['close_change'] = pandas_df['Close'] - pandas_df['Predicted_Price']

    # Assign colors dynamically
    pandas_df['low_color'] = np.where(pandas_df['low_change'] < 0, 'red', 'green')
    pandas_df['high_color'] = np.where(pandas_df['high_change'] < 0, 'red', 'green')
    pandas_df['close_color'] = np.where(pandas_df['close_change'] < 0, 'red', 'green')

    # Sort for proper order
    pandas_df.sort_values(['symbol', 'Date'], inplace=True)

    # Define metrics
    metrics = ['low_change', 'high_change', 'close_change']
    titles = ['Low Price Predictions', 'High Price Predictions', 'Close Price Predictions']
    actual_columns = ['Low', 'High', 'Close']
    predicted_columns = ['pred_Low', 'pred_High', 'Predicted_Price']
    color_columns = ['low_color', 'high_color', 'close_color']

    for symbol in pandas_df['symbol'].unique():
        symbol_df = pandas_df[pandas_df['symbol'] == symbol].reset_index(drop=True)

        fig, axes = plt.subplots(1, 3, figsize=(18, 5), dpi=80, sharey=True)

        # Use index values for evenly spaced y-axis positions
        y_positions = np.arange(len(symbol_df))

        for i, ax in enumerate(axes):
            metric = metrics[i]
            actual_col = actual_columns[i]
            pred_col = predicted_columns[i]
            color_col = color_columns[i]

            # Scatter plot for change values
            ax.scatter(symbol_df[metric], y_positions, color=symbol_df[color_col], s=80, alpha=0.7)

            # Add labels with change values
            for x, y_idx, actual, pred in zip(symbol_df[metric], y_positions, symbol_df[actual_col], symbol_df[pred_col]):
                label = f"{x:.2f} ({symbol}, {actual:.2f}, {pred:.2f})"
                ax.text(x, y_idx, label, ha='right' if x < 0 else 'left',
                        va='center', fontdict={'color': 'red' if x < 0 else 'green', 'size': 10})

            # Formatting
            ax.set_title(f"{titles[i]} ({symbol})", fontsize=14)
            ax.grid(linestyle='--', alpha=0.5)
            ax.set_xlim(symbol_df[metric].min() - 0.5, symbol_df[metric].max() + 0.5)
            ax.set_xlabel('Change Value')

            # Use index-based y-axis for even spacing
            ax.set_yticks(y_positions)
            ax.set_yticklabels(symbol_df['Date'].astype(str), fontsize=10, rotation=0)  # Ensure correct date labels

        plt.tight_layout()
        plt.show()
