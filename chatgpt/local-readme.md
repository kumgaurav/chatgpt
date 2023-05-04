conda create -n chatgpt python=3.7 h5py imageio jupyter matplotlib numpy tqdm pandas seaborn tensorflow pytorch torchvision
conda activate chatgpt

pip install -r requirement.txt

conda install -c conda-forge notebook
conda install -c conda-forge nb_conda_kernels
conda install -c conda-forge jupyterlab
conda install -c conda-forge nb_conda_kernels

conda install -c conda-forge jupyter_contrib_nbextensions

conda remove -n chatgpt --all