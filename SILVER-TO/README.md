**SILVER**

SILVER is a generic electricity network optimization tool written in Python based on object-oriented programming. It has been designed to be adaptable in different dimensions: temporal, spatial, technology representation and market design. 


**Getting Started**

Please read SILVER user manual PDF available in SILVER folder


SILVER was developed by [the Sustainable Energy Systems Integration & Transitions Group](https://sesit.cive.uvic.ca/)

**Authors**


- Madeleine McPherson
- Mohammadali Saffari
- Mohammad Miri
- Jacob Monroe
- Evan Dungate
- Ahnaf Ahmed



**Note**

SILVER is split up into 4 stages. 
- Inputs processing
- Price Setting OPF
- Unit Commitment
- Realtime OPF


Input processing is run first followed by a loop of price setting opf then unit commitment, then realtime opf. The loop will run for each hour commitment period in your specified time range that you can set in the configVariables.ini (SILVER/SILVER_DATA/user_inputs/configVariables.ini) file.

Within price setting opf and realtime opf, LPs will be created and solved for each hour in the commit period


Input file examples (and example outputs) are contained in the repo with the SS scenario name


Basic Setup:

1. install requirements
    1. [anaconda](https://docs.anaconda.com/anaconda/install/index.html)
    2. [glpk](https://winglpk.sourceforge.net/)
NOTE: you can use CPLEX as a better alternative, however you will need to fix a pyomo bug     illustrated in the user manual


2. create the python environment specified by [silver_env.yaml](https://gitlab.com/McPherson/silver/-/blob/main/silver_env.yaml) this can be done by opening an anaconda prompt and runnning the command  
```console
    conda env create -f silver_env.yaml  
```
3. Once the environment is setup activate the environment with the command
```console
    conda activate silver_env
```
4. In this environment navigate a command prompt to ..\silver\SILVER_Code where you will find SILVER_VER_18.py

5. run the command  
```console
    python SILVER_VER_18.py
```
6. a pre loaded configuration of silver with the scenario name "SS" should run

7. to configure you're own silver scenario change the relevant input files instructions on how to do this are contained in the user manual
