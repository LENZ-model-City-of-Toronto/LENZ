"""Holds progress variables for SILVER"""
UC_LMP = False

# Used by powersystems.py to determine demand response operation. Dr
# is  0 in first OPF, solved in UC, and set in second OPF (smart
# technologies (inc. storage) optimize over day)
OPF_Price = True

opf_variable = 'price'  # 'price' or 'final' for first and second opf respectively
