## Create env with dependency
    conda env create -f coursera-config.yml

## Remove env
conda remove --name coursera-jaxlib --all

## Some additional packages
python -m pip install jax-metal
