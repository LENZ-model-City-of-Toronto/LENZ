#####################################################################
__version__ = "SILVER_VER_18"
#####################################################################

# VERSION CONTROL: VER_14 for Ontario Analysis; VER_15 for VRE Characterization
# Changes from VER_14 to VER_15:
# 		> Only running UC portion of model, not first OPF (will get marginal prices from NCL) or final OPF (don't care about transmission)
#		> Removed: 	> forecasted demand	
#					> demand by region / city
# 					> OPF runs
#					> changed UC so that it uses real VRE data, not forecasted VRE data:
#							# in call_mp - changed so that VRE generator refers to real istead of forecasted name
#							# in main_model.py def append_vre - removed '_fc' from schedule filename
#		> Changed from always optimizing UC over 24 hours to making it flexible; now it can optimize over whatever is specifieid by hours_committment varible
# 					> made toggle for runtime_hours (in call_mp): if hours_commitment == 52: runtime_hours = 52
#													 else: runtime_hours = ((enddate - startdate).days)*hours_commitment + ((enddate-startdate).seconds)/3600
# 					> made toggle for either hourly VRE resource data (8760 rows) or seasonal VRE resource (52 rows) in production_ts
#					> made toggle for either hourly demand data (8760 rows) or seasonal demand data (52 rows) in setup_call_for_week
#		> Added in functionality to read in seasonal storage amount from csv file instead of being hard coded in powersystems.py
# 		> model_results_analysis.py: commented out functions that read results from OPF results since we are just
#									 running UC for the VRE characterization paper, and added in the references to the UC excel 
# 
