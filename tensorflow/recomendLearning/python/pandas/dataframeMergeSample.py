import pandas as pd
import numpy as np

# Create a sample DataFrame
data1 = {
    'ID': [1, 2, 3, 4, 5],
    'Name': ['Alice', 'Bob', 'Charlie', 'David', 'Eva']
}

df1 = pd.DataFrame(data1)

# Create a sample NumPy array with its own header column
array_data = np.array([
    ['A1', 25],
    ['B2', 30],
    ['C3', 22],
    ['D4', 35],
    ['E5', 28]
])

# Convert the NumPy array to a pandas DataFrame with custom header
header = ['Header1', 'Header2']
df2 = pd.DataFrame(array_data, columns=header)

# Concatenate the two DataFrames horizontally
result_df = pd.concat([df1, df2], axis=1)

# Print the concatenated DataFrame
print(result_df)
