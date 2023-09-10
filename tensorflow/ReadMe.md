15 Tips and Tricks for Jupyter Notebook that will ease your Coding Experience
Optimize your work in Jupyter Notebook using these hacks

1. Calculate the time of execution of a cell:
One can calculate the time of execution of a jupyter notebook cell using magic command at the beginning of the cell. It calculates the wall time that can be referred to as the total time required to execute that cell.
2. Progress Bar:
One can use a python external library to create a progress bar, that can give live updates of the progress of code. It keeps the user informed about the status of a running code script. You can get the Github repository of library here.

First, you need to install tqdm library,

`pip3 install tqdm`

3. Automatic Code Formatter:
Using nb_black library, one can format a code snippet in a cell to a proper format. Sometimes the code snippet in a jupyter notebook cell is not well-formatted, this library helps to attain proper formatting of the code snippet.

nb_black is a simple extension for Jupyter Notebook and Jupyter Lab to beautify Python code automatically.

Installation of the library:

`pip3 install nb_black`

Usage for Jupyter Notebook:

`%load_ext nb_black`




