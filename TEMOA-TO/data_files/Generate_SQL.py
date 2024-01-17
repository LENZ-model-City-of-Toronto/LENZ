from sre_constants import GROUPREF
from turtle import left, update
import pandas as pd
from IPython.display import display
from fileinput import close
import os


#this function filters the data and creates txt files that will be used to generate the SQL data
def Generate_SQL(Folder, adress, Label, Adress_data, Adress_energy, Adress_policy):

    Input_path = Folder + adress + 'Input/'
    Output_path=Folder + adress + 'Output/'

    Scenario = ['import_data','',
    '0_Base_Model',
    '1_Business_As_Usual',
    '2_Business_As_Planned',
    '3_Net-zero_2050',
    '4_Net-zero_2040',
    '5_Net-zero_2050_PureOpt',
    '6_Net-zero_2040_PureOpt',
    '7_Target-based_PureOpt',
    '8_My_Scenario'
    ]

    # Get the adress data 
    adress_data=''
    for element in adress.split('/'):
        if element not in Scenario :
            adress_data = adress_data  + element +'/'

    if os.path.exists(Input_path):

        # Empty all output folder
        for output in os.listdir(Output_path) :
            os.remove(Output_path + output)

        # Write output files    
        for file in os.listdir(Input_path):
            df=pd.read_csv(Input_path + file, header=0)
            p=len(df.columns)

            #find the table name using the name of files. If reading from parameters folder , splits text with ., other folders it splits with _ and .
            if adress_data == '0_Parameters/':
                table_name=file.split(".")[0]
            elif 'tech_groups' in file :
                table_name='tech_groups'
            elif 'tech_flex' in file :
                table_name='tech_flex'
            elif 'tech_curtailement' in file :
                table_name='tech_curtailement'
            else:
                table_name=file.split("_")[-1].split(".")[0]
            per='null'
            vin='null'
            #find the columns of vintage or period. Some files don't have any so they will be ignored
            for i in df.columns :
                if i=='periods':
                    per=df.columns.get_loc(i)
                if i=='vintage':
                    vin=df.columns.get_loc(i)


            #When running full model, the if does not have an impact. It is only used when running the model for some sectors only. It removes rows in the files about
            # the sectors that have been ignored (list of labels to be included is compiled in main code)
            
            if ((adress_data in Adress_energy)|(adress_data in Adress_policy)) :
                #for labels which don't have comodities in the label name (ie technologies)
                if table_name in ['MinGenGroupTarget','MaxGenGroupTarget','groups']:
                    if ('PASTRA' not in Label)&('FRETRA' not in Label)&('LDITRA' not in Label)&('PUBTRA' not in Label):
                        pattern = '|'.join(Label + ["BMTNIMP"])
                    else:
                        pattern = '|'.join(Label + ["BMTNIMP","BDSLIMP"])
                    df=df.loc[df['group_name'].str.contains(pattern, na=False)]
                

                elif table_name not in ["commodities","EmissionLimit","CostEmissions"]:
                    pattern = '|'.join(Label)
                    df=df.loc[df['tech'].str.contains(pattern, na=False)]
                
                #searching for commodities is limited to some folders because the trm commodity is only found in those folders
                elif adress_data in ['4_Energy/3_Thermal_district_energy/1_Existing_system/',
                '4_Energy/3_Thermal_district_energy/2_New_technology/',
                '4_Energy/2_Electricity_district_energy/2_New_technology/']:
                    pattern = '|'.join(Label)
                    df=df.loc[df['comm_name'].str.contains(pattern, na=False)]
                
                #for trade energy files, specifically waste, delete all commodities which are labeled for export. All other sectors do not have exports (excepted industrial)
                if (adress_data =='4_Energy/1_Trade_energy/1_Existing_system/')|(adress_data in Adress_policy):
                    if ('3_Waste/1_Water_Waste/1_Existing_system/' not in Adress_data)&('3_Waste/2_Solid_Waste/1_Existing_system/' not in Adress_data) :
                        if table_name == "commodities":
                            df=df.loc[(~df['comm_name'].str.contains('EXP'))|(df['comm_name'].str.contains('ETHOSEXP'))|(df['comm_name'].str.contains('EXPETHOS'))]
                        elif table_name not in ["groups","MaxGenGroupTarget","MinGenGroupTarget","EmissionLimit","CostEmissions"]:
                            df=df.loc[(~df['tech'].str.contains('EXP'))|(df['tech'].str.contains('ETHOSEXP'))|(df['tech'].str.contains('EXPETHOS'))]

                            if table_name=='Efficiency':
                                df=df.loc[(~df['output_comm'].str.contains('EXP'))|(df['output_comm'].str.contains('ETHOSEXP'))|(df['input_comm'].str.contains('ETHOSEXP'))|(df['output_comm'].str.contains('EXPETHOS'))|(df['input_comm'].str.contains('EXPETHOS'))]
                                df=df.loc[(~df['input_comm'].str.contains('EXP'))|(df['output_comm'].str.contains('ETHOSEXP'))|(df['input_comm'].str.contains('ETHOSEXP'))|(df['output_comm'].str.contains('EXPETHOS'))|(df['input_comm'].str.contains('EXPETHOS'))]


            #create output files
            with open(Output_path + table_name +'.txt', "w"):
                for index, row in df.iterrows():
                    
                    #for tables that do not have period or vintage, there is no need to filter rows, and the entire table is converted to output file
                    if (per=='null'):
                        if (vin=='null'):
                            output= 'INSERT INTO' +'`'+ table_name +'`'+'VALUES' +'(' 
                            for k in range (0,p):
                                if(str(row[k]).replace('.','',1).replace('-','',1).isdigit()):
                                    output= output + str(row[k])  
                                else :
                                    output= output+ "'" + str(row[k]) + "'" 
                                if (k!=p-1):
                                    output= output+ ','
                            output= output + ');'
                            print(output,file= open(Output_path + table_name +'.txt', 'a'))
                    
                    #if table has vintage only, select rows which have vintage that are included in period table from paramaters file
                        elif (row[vin] in Period['t_periods'].values):
                            output= 'INSERT INTO' +'`'+ table_name +'`'+'VALUES' +'(' 
                            for k in range (0,p):
                                if(str(row[k]).replace('.','',1).replace('-','',1).isdigit()):
                                    output= output + str(row[k])  
                                else :
                                    output= output+ "'" + str(row[k]) + "'" 
                                if (k!=p-1):
                                    output= output+ ','
                            output= output + ');'
                            print(output,file= open(Output_path + table_name +'.txt', 'a'))
                    
                    #if table has period only, select rows which have periods that are included in period table from paramaters file
                    elif (row[per] in Period['t_periods'].values):
                        if (vin=='null'):
                            output= 'INSERT INTO' +'`'+ table_name +'`'+'VALUES' +'(' 
                            for k in range (0,p):
                                if(str(row[k]).replace('.','',1).replace('-','',1).isdigit()):
                                    output= output + str(row[k])  
                                else :
                                    output= output+ "'" + str(row[k]) + "'" 
                                if (k!=p-1):
                                    output= output+ ','
                            output= output + ');'
                            print(output,file= open(Output_path + table_name +'.txt', 'a'))
                        
                        #if table has period and vintage, select rows which have periods vintages that are included in period table from paramaters file
                        elif (row[vin] in Period['t_periods'].values):
                            output= 'INSERT INTO' +'`'+ table_name +'`'+'VALUES' +'(' 
                            for k in range (0,p):
                                if(str(row[k]).replace('.','',1).replace('-','',1).isdigit()):
                                    output= output + str(row[k])  
                                else :
                                    output= output+ "'" + str(row[k]) + "'" 
                                if (k!=p-1):
                                    output= output+ ','
                            output= output + ');'
                            print(output,file= open(Output_path + table_name +'.txt', 'a'))
    return 0


#creates SQL file with a name based on name of file specified in import_address.TXT
def Initiate_SQL(Folder, output_name):
    
    open(Folder + output_name, 'w')
    Output = open(Folder + output_name, 'r+')
    raw = open(Folder + 'raw.sql', 'r')


    # Clean file if it already exists
    Output.seek(0)
    Output.truncate(0)

    # Copy raw file into SQL file
    raw_lines = raw.readlines()
    lines_out= Output.readlines()

    for line in reversed(raw_lines): 
        lines_out.insert(0,line)

    Output.writelines(lines_out)
    Output.close()
    raw.close()



#funtion to update the raw SQL file and insert data into it and then saving it as sql file under the name specified in import_address.TXT
def Import_to_SQL(Folder, Adress_scen, adress_data ,output_name):

    Output=open(Folder + output_name, 'r+')
    lines_out= Output.readlines()

    Path_base = Folder + Adress_scen[0] + adress_data + 'Output/'
    if len(Adress_scen)==2:
        Path_scen = Folder + Adress_scen[1] + adress_data + 'Output/'
    else:
        Path_scen ='n/a'
        
    # Import in case of possible rewritting
    if ((os.path.exists(Path_base))&(os.path.exists(Path_scen))):        
        display(adress_data)

        # Keep base file only if not existing in the scenario folder
        for file in os.listdir(Path_base):
            if file not in os.listdir(Path_scen):
                reach=False
                next=False
                Input=open(Path_base + file, 'r')
                lines_in= Input.readlines()
            
                cpt=0

                for line in lines_out:
                    if (next==True)&(reach==True):
                        for add_line in lines_in:
                            lines_out.insert(cpt, add_line)
                        next = False
                        reach = False
                        if Path_base.split("/")[-3] == "0_Parameters":
                            lines_out.insert(cpt,'-- Simulation parameters' + '\n')
                        else:
                            lines_out.insert(cpt,'-- ' + Path_base.split("/")[-6] + ' scenario ' + Path_base.split("/")[-3]  +' for ' + Path_base.split("/")[-4]  +' sub-sector '+  file.replace('.txt','')+' parameters; \n')
                        break
                
                    if 'CREATE TABLE "' + file.replace('.txt','')+'"' in line :
                        reach = True

                    if (');' in line)&(reach==True):
                        next=True

                    Input.close()
                    cpt+=1

        # keep all scenario folder data             
        for file in os.listdir(Path_scen):
            reach=False
            next=False
            Input=open(Path_scen + file, 'r')
            lines_in= Input.readlines()
            
            cpt=0

            for line in lines_out:
                if (next==True)&(reach==True):
                    for add_line in lines_in:
                        lines_out.insert(cpt, add_line)
                    next = False
                    reach = False
                    lines_out.insert(cpt,'-- ' + Path_scen.split("/")[-6] + ' scenario '+ Path_scen.split("/")[-3]  +' for ' + Path_scen.split("/")[-4]  +' sub-sector '+  file.replace('.txt','')+' parameters; \n')
                    break
                
                if 'CREATE TABLE "' + file.replace('.txt','')+'"' in line :
                    reach = True

                if (');' in line)&(reach==True):
                    next=True

                Input.close()
                cpt+=1

    # Import when Path scen folder not existing
    elif ((os.path.exists(Path_base))&(not os.path.exists(Path_scen))):        
        display(adress_data)

        # keep all base folder data             
        for file in os.listdir(Path_base):
            reach=False
            next=False
            Input=open(Path_base + file, 'r')
            lines_in= Input.readlines()
            
            cpt=0

            for line in lines_out:
                if (next==True)&(reach==True):
                    for add_line in lines_in:
                        lines_out.insert(cpt, add_line)
                    next = False
                    reach = False
                    if Path_base.split("/")[-3] == "0_Parameters":
                        lines_out.insert(cpt,'-- Simulation parameters' + '\n')
                    else:
                        lines_out.insert(cpt,'-- ' + Path_base.split("/")[-6] + ' scenario ' + Path_base.split("/")[-3]  +' for ' + Path_base.split("/")[-4]  +' sub-sector '+  file.replace('.txt','')+' parameters; \n')
                    break
                
                if 'CREATE TABLE "' + file.replace('.txt','')+'"' in line :
                    reach = True

                if (');' in line)&(reach==True):
                    next=True

                Input.close()
                cpt+=1

    # Import when Path base folder not existing
    elif ((not os.path.exists(Path_base))&(os.path.exists(Path_scen))):        
        display(adress_data)

        # keep all scenario folder data             
        for file in os.listdir(Path_scen):
            reach=False
            next=False
            Input=open(Path_scen + file, 'r')
            lines_in= Input.readlines()
            
            cpt=0

            for line in lines_out:
                if (next==True)&(reach==True):
                    for add_line in lines_in:
                        lines_out.insert(cpt, add_line)
                    next = False
                    reach = False
                    lines_out.insert(cpt,'-- ' + Path_scen.split("/")[-6] + ' scenario '+ Path_scen.split("/")[-3]  +' for ' + Path_scen.split("/")[-4]  +' sub-sector '+  file.replace('.txt','')+' parameters; \n')
                    break
                
                if 'CREATE TABLE "' + file.replace('.txt','')+'"' in line :
                    reach = True

                if (');' in line)&(reach==True):
                    next=True

                Input.close()
                cpt+=1

    Output.seek(0)
    Output.truncate()
    Output.writelines(lines_out)
    Output.close()

    return 0


#creates SQL file with a name based on name of file specified in import_address.TXT
def Delete_duplicate_SQL(Folder, output_name):
    
    Output_clean = open(Folder + output_name +'bis', 'w')

    lines_seen = set() # holds lines already seen
    for line in open(Folder + output_name, 'r'):
        if (line not in lines_seen): # not a duplicate
            if ('tech_groups' in line):
                lines_seen.add(line)
            Output_clean.write(line)
    Output_clean.close()

    os.remove(Folder + output_name)
    old_file = os.path.join(Folder, output_name +'bis')
    new_file = os.path.join(Folder, output_name)
    os.rename(old_file, new_file)



#Main function starts here
Adress=[]
Adress_scen=[]
Adress_data=[]
Adress_policy=[]
Adress_energy=[]

Folder = os.path.realpath(__file__)[:os.path.realpath(__file__).find(os. path. basename(__file__))]
Output=open(Folder + "Import_adress.txt", 'r+')

#Address of file with periods to be included
Period=pd.read_csv(Folder + 'import_data/0_Base_Model/0_Parameters/Input/time_periods.csv',header = 0)


#for loop runs through each line of the text file and determines which sector and subsector to import. Lines starting with # are excluded
name=False
lines_out= Output.readlines()
Sector= ['0_Parameters','1_Building','2_Transport','3_Waste','4_Energy','5_Policy']
for line in lines_out:
    if '#' not in line : 
        if name == True:
            output_name=line.replace('\n','')+ ".sql"
            name=False
        if 'import_data' in line:
            Adress_scen.append(line.replace('\n',''))
        if '5_Policy' in line:
            Adress_policy.append(line.replace('\n',''))
        if '4_Energy' in line:
            Adress_energy.append(line.replace('\n',''))
        if '//Export_name//' in line :
            name=True
        if any(sect in line for sect in Sector ) :
            Adress_data.append(line.replace('\n',''))

# Cross merge scenario and data adresses
for scenario in Adress_scen:
    for data in Adress_data:
        Adress.append(scenario + data)

#find labels of sectors included
Label=['TOETHOS']
if '1_Building/1_Residential_building/1_Existing_system/' in Adress_data :
    Label.append('RESBDG')
if '1_Building/2_Commercial_building/1_Existing_system/' in Adress_data :
    Label.append('COMBDG')
if '1_Building/3_Industrial_building/1_Existing_system/' in Adress_data :
    Label.append('INDBDG')
if '1_Building/4_Public_building/1_Existing_system/' in Adress_data :
    Label.append('PUBBDG')
if '2_Transport/1_Freight_transportation/1_Existing_system/' in Adress_data :
    Label.append('FRETRA')
if '2_Transport/2_Passenger_transportation/1_Existing_system/' in Adress_data :
    Label.append('PASTRA')
if '2_Transport/3_Long_Distance_transportation/1_Existing_system/' in Adress_data :
    Label.append('LDITRA')
if '2_Transport/4_Public_transportation/1_Existing_system/' in Adress_data :
    Label.append('PUBTRA')
if '3_Waste/1_Water_Waste/1_Existing_system/' in Adress_data :
    Label.append('WATWAS')
if '3_Waste/2_Solid_Waste/1_Existing_system/' in Adress_data :
    Label.append('SLDWAS')


for adress in Adress:
    Generate_SQL(Folder, adress, Label, Adress_data, Adress_energy, Adress_policy)

Initiate_SQL(Folder, output_name)

for adress_data in Adress_data:
    Import_to_SQL(Folder ,Adress_scen, adress_data, output_name)

Delete_duplicate_SQL(Folder, output_name)
