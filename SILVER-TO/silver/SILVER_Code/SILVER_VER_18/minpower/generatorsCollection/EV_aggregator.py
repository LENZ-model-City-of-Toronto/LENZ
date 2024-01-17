from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from builtins import str
from builtins import range
import pandas as pd
from ..config import user_config
from ..commonscripts import update_attributes, bool_to_int
from SILVER_VER_18.config_variables import *
from ..schedule import is_init
from .. import bidding
from ..silver_variables import FOLDER_UC, PATH_MODEL_INPUTS
from .baseGenerator import BaseGenerator


class EV_aggregator(BaseGenerator):
    '''
    A generator model.

    :param pmin: minimum real power
    :param pmax: maximum real power
    :param minuptime: min. time after commitment in on status (hours)
    :param mindowntime: min. time after de-commitment in off status (hours)
    :param rampratemax: max. positive change in real power over 1hr (MW/hr)
    :param rampratemin: max. negative change in real power over 1hr (MW/hr)
    :param startupramplimit: max. positive change in real power over the
        first hour after startup (MW/hr)
    :param shutdownramplimit: max. negative change in real power over the
        last hour before shutdown (MW/hr)
    :param costcurveequation: text describing a polynomial cost curve ($/MWh)
      see :meth:`~bidding.parsePolynomial` for more.
    :param heatratestring: text describing a polynomial heat rate curve (MBTU/MW).
        converts to cost curve when multiplied by fuelcost.
    :param fuelcost: cost of fuel ($/MBTU)
    :param startupcost: cost to commit ($)
    :param shutdowncost: cost to de-commit ($)
    :param mustrun: flag that forces commimtent to be on

    :param name: name of the generator
    :param index: numbering of the generator
    :param bus: bus name that the generator is connected to
    '''

    def __init__(self, kind='generic',
                 pmin=0, pmax=500,
                 minuptime=0, mindowntime=0,
                 rampratemax=None, rampratemin=None,
                 costcurveequation='20P',
                 heatrateequation=None, fuelcost=1,
                 bid_points=None,
                 noloadcost=0,
                 startupcost=0, shutdowncost=0,
                 startupramplimit=None,
                 shutdownramplimit=None,
                 faststart=False,
                 mustrun=False,
                 name='', index=None, bus=None,
                 unpluggedhours=None,
                 sheddingallowed=True,
                 storagecapacitymax=None,
                 tripenergy=None,
                 ):

        update_attributes(self, locals())  # load in inputs

        # The formulation below requires that startup ramp limits are set.
        # The defaults are the normal ramp rate limits,
        # unless Pmin so high and Rmin/max small that startup is infeasible.
        # In that case the default is Pmin.

        if (self.startupramplimit is None) and (self.rampratemax is not None):
            self.startupramplimit = max(self.pmin, self.rampratemax)

        if (self.shutdownramplimit is None) and (self.rampratemin is not None):
            self.shutdownramplimit = min(-1 * self.pmin, self.rampratemin)

        self.fuelcost = float(fuelcost)
        self.is_controllable = True
        self.is_stochastic = False
        self.commitment_problem = True
        self.build_cost_model()
        self.init_optimization()
        self.unpluggedhours = unpluggedhours.split(",")
        self.tripenergy = tripenergy
        self.site_independent = pd.read_excel(PATH_MODEL_INPUTS, sheet_name='site independent', index_col=0)
        self.efficiency = self.site_independent[3]['EV']

    ####################################
    # OBJECTIVE FUNCTION
    ####################################

    def operatingcost(self, time=None, scenario=None, evaluate=False):
        '''cost of power production at time based on price setting OPF which determes hourly electricity system price'''

        if self.commitment_problem is True:
            ''' import cost from csv file created in 1st (price-setting) OPF, will only be used in UC '''

            # TODO: possibly turn this into a global variable for UC once set
            if RUN_OPF:
                self.storage_cost_list = pd.read_csv(FOLDER_UC / 'storage_cost_opf.csv', index_col=0)
                cost = self.storage_cost_list.loc[time][self.index]
            else:
                cost = 1
            return cost * self.power(time)

        if self.commitment_problem is False:
            ''' Return the storage_cost(time)*storage.power(time) '''
            # The OPF status of all generators is always 1, therefore this always just returns the price of the most expensive generator'''
            return self.bids.output(time, scenario=scenario, evaluate=evaluate)

    ####################################
    # OPTIMIZATION VARIABLES
    ####################################

    def power_in(self, time=None, scenario=None):
        ''' power input (charging) at time '''
        if time is not None and is_init(time):
            return self.initial_power_in
        else:
            return self.get_variable('power_in', time, scenario=scenario, indexed=True)

    def power_in_available(self, time=None, scenario=None):
        ''' charging power availble (constrained by pmax, ramprate, ...) at time '''
        if time is not None and is_init(time):
            return self.initial_power
        var_name = 'power_in_available' if self.commitment_problem \
            and self.reserve_required else 'power_in'
        return self.get_variable(var_name, time, scenario=scenario, indexed=True)

    def status_in(self, time=None, scenario=None):
        ''' charging on/off status_in (pumping) at time '''
        if self.commitment_problem or user_config.dispatch_decommit_allowed:
            if time is not None and is_init(time):
                return self.initial_status
            else:
                return self.get_variable('status_in', time, scenario=scenario, indexed=True)
        else:
            return 1

    def absolute_power(self, time=None, sceario=None):
        ''' the absolute value of generating (+ve) and charging (-ve) '''
        absolute_power = self.get_variable('absolute power', time, scenario=None, indexed=True)
        return absolute_power

    def EV_content(self, time=None, scenario=None):
        ''' amount of electricity (MWh) stored in EV vehicles in any hour '''
        return self.get_variable('EV_content', time, scenario=scenario, indexed=True)

    def create_variables(self, times):
        '''
        Create the optimization variables for a generator over all times.
        Also create the :class:`bidding.Bid` objects and their variables.
        '''

        self.commitment_problem = len(times) > 1
        self.add_variable('power', index=times.set, low=0, high=self.pmax)
        self.add_variable('power_in', index=times.set, low=(-1) * self.pmax, high=0)
        self.add_variable('EV_content', index=times.set, low=0, high=self.storagecapacitymax)
        self.add_variable('absolute_power', index=times.set, low=0)

        # if self.commitment_problem or user_config.dispatch_decommit_allowed:
        self.add_variable('status', index=times.set, kind='Binary',
                          fixed_value=1 if self.mustrun else None)

        self.add_variable('status_in', index=times.set, kind='Binary',
                          fixed_value=1 if self.mustrun else None)

        if self.commitment_problem:
            # power_available exists for easier reserve requirement
            self.reserve_required = self._parent_problem().reserve_required
            if self.reserve_required:
                self.add_variable(
                    'power_available', index=times.set, low=0, high=self.pmax)
                self.add_variable(
                    'power_in_available', index=times.set, low=(-1) * self.pmax, high=0)
            if self.startupcost > 0:
                self.add_variable('startupcost', index=times.set,
                                  low=0, high=self.startupcost)
            if self.shutdowncost > 0:
                self.add_variable('shutdowncost', index=times.set,
                                  low=0, high=self.shutdowncost)

        self.bids = bidding.Bid(times=times, **self.bid_params)
        return

    #####################################
    # CONSTRAINTS (and related functions)
    #####################################

    # TODO, make this code work for multiple EV plants
    def dPrev_EV_content(self, t):
        try:
            EV_plants = pd.read_csv(FOLDER_UC / 'initial.csv', index_col=1)
            self.EV_content_previous = EV_plants['storage content'][self.name]
        except:
            print(" COULD NOT READ STORAGE CONTENT FROM PREVIOUS DAY - restting value to zero")
            self.EV_content_previous = 0

        return float(self.EV_content_previous)

    def pumping(self, time=None, scenario=None):
        return self.power(time) + self.power_in(time)

    def generating(self, time=None, scenario=None):
        return self.power(time) + (-1) * (self.power_in(time))

    def create_constraints(self, times):
        '''create the optimization constraints for a generator over all times'''

        ''' generic constraints that pertain to all generators '''
        if self.commitment_problem:
            # set initial and final time constraints

            tInitial = times.initialTimestr
            tEnd = len(times)

            self.min_up_down_time_constraints(times, tInitial, tEnd)

            self.rampRateLimitConstraints(times, tInitial)

            self.reserveConstraints(times)

            ''' limit the power capacity of charging and discharging of the PEV fleet  to be between pmin and pmax'''
            if self.pmin > 0:
                def min_power(model, t):
                    return self.power(t) >= self.status(t) * self.pmin

                self.add_constraint_set('min gen power', times.set, min_power)

                def min_power_in(model, t):
                    return self.power_in(t) <= self.status_in(t) * self.pmin * (-1)
                self.add_constraint_set('min pumping power', times.set, min_power_in)

            def max_power(model, t):
                return self.power_available(t) <= self.status(t) * self.pmax
            self.add_constraint_set('max gen power', times.set, max_power)

            def max_power_in(model, t):
                return self.power_in_available(t) >= self.status_in(t) * self.pmax * (-1)
            self.add_constraint_set('max pumping power', times.set, max_power_in)

            ''' constraints that pertain to storage facilities specifically '''

            EV_charging_hours, EV_non_charging_hours, all_hours = [], [], []
            transportation_hours = []
            [all_hours.append(str('t' + '%02d' % hour)) for hour in range(24)]

            if "-" in self.unpluggedhours[0]:  # .find("-") is True:

                unplugged_list = self.unpluggedhours[0].split("-")

                self.depart_t = unplugged_list[0]    # t08 - time in morning when vehicle departures from dock (e.g. t08)
                self.arrive_t = unplugged_list[1]
                for hour in range(int(self.depart_t.replace('t', '')), int(self.arrive_t.replace('t', ''))):
                    EV_non_charging_hours.append(str('t' + '%02d' % hour))
                next_hour = int(self.depart_t.replace('t', '')) + 1
                transportation_hours.append(str('t' + '%02d' % next_hour))

            else:

                for unpluggedhour in range(len(self.unpluggedhours)):

                    EV_non_charging_hours.append(str(unpluggedhour))

                transportation_hours = self.unpluggedhours

            ''' Inventory balance: the amount of electricity in the battery given charging / discharging '''
            def transportation_power(t):
                # if t == 't09':
                return self.tripenergy if t in transportation_hours else 0

            def EV_content_capacity(model, t):
                tPrev = self.get_tPrev(t, model, times)
                if t != 't00':
                    return self.EV_content(t) == self.EV_content(tPrev) - self.power_in(t) * self.efficiency \
                        - self.power(t) - transportation_power(t)

                dPrev_EV_content_t23 = self.dPrev_EV_content(t)
                return self.EV_content(t) == dPrev_EV_content_t23 - self.power_in(t) * self.efficiency \
                    - self.power(t) - transportation_power(t)

            self.add_constraint_set('kWh_in_EV', times.set, EV_content_capacity)

            ''' limit stored electricity to the battery capacity of the EVs'''
            def max_EV_capacity(model, t):
                tPrev = self.get_tPrev(t, model, times)

                return self.EV_content(t) <= self.storagecapacitymax

            self.add_constraint_set('max_kWh_in_EV', times.set, max_EV_capacity)

            '''EV unit can EITHER be pumping or generatin, but not both'''
            ''' create absolute_power - a varaible that always takes the absolute value of power and power_in'''
            for t in times:

                self.add_constraint('absolute pwer 1', t, self.pumping(t) <= self.absolute_power(t))
                self.add_constraint('absolute power 2', t, self.generating(t) <= self.absolute_power(t))

            def generating_or_pumping_OPF(model, t):
                return self.power(t) + (-1) * self.power_in(t) <= self.absolute_power(t)

            self.add_constraint_set('generating or pumping OPF', times.set, generating_or_pumping_OPF)

            def generating_or_pumping_UC(model, t):
                return self.status(t) + self.status_in(t) <= 1

            self.add_constraint_set('generating or pumping UC', times.set, generating_or_pumping_UC)

            ''' create lists of hours in which EV vehicles are pluged in or not plugged in based on user inputs '''

            for time in times:
                if time in EV_non_charging_hours:
                    # print "non chargin tours", time
                    expression = self.power(time) <= 0
                    # print "Constraint 18"
                    self.add_constraint('EV generation hours limits', time, expression)

                    expression_2 = self.power_in(time) >= 0
                    # print "Constraint 19"
                    self.add_constraint('EV charging hour limits', time, expression_2)

        else:   # for OPF problem, need to bound power to be within what was calculated in UC
            # note that pmin in the opf generators.csv is recoreded ast he power (+ve) or power_in(-ve) value from the UC
            # the pmax in teh opf generators.csv is recorded as the abs(power or power_in) (always +ve)
            # Note: storage formulation is different, so if this stops working, refer to storage formulation
            if self.pmin > 0:
                def max_power(model, t):
                    return self.power_available(t) <= self.pmax

                def min_power(model, t):
                    return self.power(t) >= self.pmin

                def pumping_or_generating(model, t):
                    return self.power_in_available(t) == 0

                self.add_constraint_set('max power', times.set, max_power)
                self.add_constraint_set('min power', times.set, min_power)
                self.add_constraint_set('pumping or generating opf', times.set, pumping_or_generating)
            elif self.pmin < 0:
                def max_power_in_2(model, t):
                    return self.power_in_available(t) <= self.pmin

                def min_power_in(model, t):
                    return self.power(t) >= self.pmin       # different from storage formulation

                def set_power_to_zero(model, t):
                    return self.power(t) == 0

                self.add_constraint_set('max power 2', times.set, max_power_in_2)
                self.add_constraint_set('max power 3', times.set, set_power_to_zero)
                # self.add_constraint_set('min power', times.set, min_power_in)
        return

    ####################################
    # ADDITIONAL FUNCTIONS
    ####################################

    def set_initial_condition(self, power=None, power_in=None,
                              status=True, status_in=True, hoursinstatus=1):

        self.initial_status_in = bool_to_int(status_in)
        self.initial_power_in = float(power * self.initial_status_in)

        super().set_initial_condition(power, status, hoursinstatus)
