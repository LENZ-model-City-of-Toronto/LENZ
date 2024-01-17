import configparser
config = configparser.ConfigParser(allow_no_value=True)
config['Commitment_Times'] = {'# Dates must be in the format YYYY/M/D':None,
                              'user_startdate': '2018/1/1',
                              'user_enddate': '2018/1/3',
                              'hours_commitment': '48'}

config['Run_Parameters'] = {'scenario_number': 'SS',
                            'runOPF': True,
                            'mustrun_NGCC_bio': False,
                            'UC_networked': True,
                            'merra_data_year': '2018',
                            'solver': 'cplex'}

with open('SILVER/SILVER_Data/user_inputs/configVariables.ini', 'w') as configfile:
    config.write(configfile)