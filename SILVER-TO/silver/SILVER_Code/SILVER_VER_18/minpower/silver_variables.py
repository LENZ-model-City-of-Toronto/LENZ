import os, sys, inspect
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0, parentdir)

from config_variables import FOLDER_OPF, FOLDER_PRICE_OPF, PATH, FOLDER_UC, PATH_MODEL_INPUTS
