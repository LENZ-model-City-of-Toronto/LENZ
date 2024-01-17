""" Handles user input and config variables for running SILVER """

from time import process_time

from pathlib import Path

from datetime import datetime
import configparser
import sys

# Get current working directory, and parent directories
PATHCWD = Path.cwd()
PATH = PATHCWD.parent
REPO_ROOT = PATH.parent
DATA_PATH = PATHCWD.parent.joinpath('SILVER_Data')
USER_INPUTS = DATA_PATH.joinpath('user_inputs')

# SILVER\SILVER_Data\user_inputs\configVariables.ini
config = configparser.ConfigParser()
try:
    config.read(USER_INPUTS / 'configVariables.ini')
except FileNotFoundError:
    print('configVariables.ini not found. Please check that it is in the '
          + 'SILVER_Data/user_inputs folder or run configCreate.py')
    sys.exit()


SCENARIO_NUMBER = config['Run_Parameters']['Scenario_Number']


# Output directories
FOLDER_UC = DATA_PATH.joinpath(f'Outputs_UC-{SCENARIO_NUMBER}')
FOLDER_OPF = DATA_PATH.joinpath(f'Outputs_OPF-{SCENARIO_NUMBER}')
FOLDER_PRICE_OPF = DATA_PATH.joinpath(f'Outputs_Cost_OPF-{SCENARIO_NUMBER}')
FOLDER_TO_SAVE_FINAL_RESULTS = DATA_PATH.joinpath(f'Model Results/{SCENARIO_NUMBER}')

# Input directories
PATH_HYDRO = USER_INPUTS.joinpath(f'Hydro_Data-{SCENARIO_NUMBER}')
PATH_MODEL_INPUTS = USER_INPUTS.joinpath(f'model inputs - {SCENARIO_NUMBER}.xlsx')
PATH_GENERATION = USER_INPUTS.joinpath(f'VRE_Resource_Analysis-{SCENARIO_NUMBER}')
PATH_IMPORTEXPORT = USER_INPUTS.joinpath(f'ImportExport_Data-{SCENARIO_NUMBER}')
DEMAND_SCHEDULE_FILENAME = USER_INPUTS.joinpath(f'{SCENARIO_NUMBER}_Demand_Real_Forecasted.xlsx')

# Config variables
RUN_OPF = config.getboolean('Run_Parameters', 'runOPF')    # Whether standalone UC or full OPF should be run

USER_STARTDATE = datetime.strptime(config['Commitment_Times']['user_startdate'], '%Y/%m/%d')
USER_ENDDATE = datetime.strptime(config['Commitment_Times']['user_enddate'], '%Y/%m/%d')

MUSTRUN_NGCC_BIO = config.getboolean('Run_Parameters', 'mustrun_NGCC_bio')
HOURS_COMMITMENT = int(config['Commitment_Times']['hours_commitment'])
UC_NETWORKED = config.getboolean('Run_Parameters', 'UC_networked')
MERRA_DATA_YEAR = config['Run_Parameters']['merra_data_year']
NUMBER_OF_DAYS = HOURS_COMMITMENT // 24
SOLVER = config['Run_Parameters']['solver']


def edit_minpower_config(section, key, value):
    """changes minpower config automatically deletes comments"""
    minpower = configparser.ConfigParser()
    minpower_file = PATH / 'SILVER_CODE/SILVER_VER_18/minpower/configuration/minpower.cfg'
    minpower.read(minpower_file)
    minpower[section][key] = value
    with open(minpower_file, 'w') as minpower_config:
        minpower.write(minpower_config)


edit_minpower_config('minpower', 'hours_commitment', config['Commitment_Times']['hours_commitment'])
edit_minpower_config('minpower', 'solver', config['Run_Parameters']['solver'])
