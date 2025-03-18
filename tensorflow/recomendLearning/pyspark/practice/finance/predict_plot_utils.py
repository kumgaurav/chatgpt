import matplotlib.pyplot as plt
import numpy as np
from collections import deque
import seaborn as sns

sns.set()


def plot_buy_sell(df, header):
    combine = df.copy()
    g = sns.jointplot(x="volatility", y="returns", data=combine, kind="reg", height=9, ratio=4)

    for ticker in combine.index:
        g.ax_joint.annotate(ticker, (combine.loc[ticker, 'volatility'], combine.loc[ticker, 'returns']),
                            xytext=(5, 5), textcoords='offset points', fontsize=8)

    g.ax_joint.text(combine['volatility'].mean(), combine['returns'].max(), 'BUY',
                    fontsize=20, color='green', ha='center', va='bottom')
    g.ax_joint.text(combine['volatility'].mean(), combine['returns'].min(), 'SELL',
                    fontsize=20, color='red', ha='center', va='top')

    g.ax_joint.set_title(header, fontsize=16)
    g.ax_joint.set_xlabel('Volatility', fontsize=12)
    g.ax_joint.set_ylabel('Returns', fontsize=12)

    plt.tight_layout()
    plt.show()
